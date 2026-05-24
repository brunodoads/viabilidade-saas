"""
Celery Tasks — Pipeline de Processamento de Catálogos.

Design MVP:
- 1 task principal por catálogo: process_catalog_task
- Execução sequencial: scout → market → finance → strategy
- Cada etapa atualiza catalog.status no banco
- Falha em produto individual não cancela os demais
- Retry automático em falhas de infra (ex: banco fora do ar)
"""

import logging
import uuid

from celery import Task

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class PipelineTask(Task):
    """Task base com tratamento de erro padronizado."""

    abstract = True

    def on_failure(self, exc: Exception, task_id: str, args, kwargs, einfo) -> None:
        logger.error(
            "Task %s falhou | task_id=%s | erro=%s",
            self.name,
            task_id,
            str(exc),
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="tasks.process_catalog",
    max_retries=3,
    default_retry_delay=60,  # 60s entre retries
    # 403 produtos x ~20s/Apify = ~2.25h. Margem de 2x para catalogos grandes.
    soft_time_limit=18000,   # 5h -- timeout soft (lanca SoftTimeLimitExceeded)
    time_limit=18600,        # 5h10m -- timeout hard (mata o processo)
)
def process_catalog_task(self: Task, catalog_id_str: str) -> dict:
    """
    Task principal: orquestra o pipeline completo para um catalogo.

    Etapas:
    1. PARSING    -- scout_service.parse_catalog()
    2. RESEARCHING -- market_service.research_catalog()
    3. ANALYZING  -- finance_service.analyze_catalog()
    4. SCORING    -- strategy_service.score_catalog()
    5. READY      -- pipeline concluido

    Em caso de erro:
    - Atualiza catalog.status = ERROR com mensagem
    - Salva no banco para o usuario ver via polling
    - Re-lanca excecao para Celery registrar o failure
    """
    catalog_id = uuid.UUID(catalog_id_str)

    # Import local com diagnostico explicito (igual ao reprocess_analysis_task)
    try:
        from app.db.session import SessionLocal
        from app.models.catalog import CatalogStatus
        from app.repositories.catalog_repo import CatalogRepository
        from app.services import finance_service, market_service, scout_service, strategy_service
    except Exception as import_exc:
        import traceback
        import psycopg2
        err_msg = f"ImportError na task: {type(import_exc).__name__}: {import_exc}\n{traceback.format_exc()[-500:]}"
        logger.error("IMPORT FALHOU em process_catalog_task: %s", err_msg)
        try:
            _conn = psycopg2.connect(
                host="aws-1-sa-east-1.pooler.supabase.com", port=5432,
                dbname="postgres", user="postgres.gjtzgbvwoiezpiegwvnx",
                password="DZoTXeBpguf7ejiUjr2ztNc5",
            )
            _cur = _conn.cursor()
            _cur.execute(
                "UPDATE catalogs SET status='ERROR', error_message=%s WHERE id=%s",
                (err_msg[:1000], str(catalog_id)),
            )
            _conn.commit()
            _cur.close()
            _conn.close()
        except Exception as db_exc:
            logger.error("Falha ao escrever ImportError no banco: %s", db_exc)
        raise import_exc

    db = SessionLocal()
    try:
        catalog_repo = CatalogRepository(db)
        catalog = catalog_repo.get_by_id(catalog_id)

        if catalog is None:
            logger.error("Catalogo %s nao encontrado", catalog_id)
            return {"status": "not_found", "catalog_id": catalog_id_str}

        logger.info("Iniciando pipeline | catalog_id=%s | arquivo=%s", catalog_id, catalog.original_filename)

        # Materializar arquivo em disco (cross-container)
        # O backend salva o arquivo no SEU container (/app/uploads/).
        # O worker roda em container SEPARADO -- sem filesystem compartilhado.
        # Solucao MVP: file_content esta no PostgreSQL. Se o arquivo nao existir
        # localmente, escrevemos do DB para um path temporario antes do parsing.
        import tempfile
        from pathlib import Path as _Path

        _file_path = _Path(catalog.file_path)
        _temp_file = None

        if not _file_path.exists() and catalog.file_content:
            # Criar arquivo temporario com a extensao original
            suffix = _file_path.suffix or ".bin"
            _temp_file = tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False, prefix="catalog_"
            )
            _temp_file.write(catalog.file_content)
            _temp_file.flush()
            _temp_file.close()
            catalog.file_path = _temp_file.name
            logger.info(
                "Arquivo materializado do DB -> %s | catalog_id=%s",
                _temp_file.name, catalog_id
            )

        # Etapa 1: PARSING
        from app.services.parse_result import ParseConfidence

        try:
            catalog_repo.update_status(catalog, CatalogStatus.PARSING)
            parse_result = scout_service.parse_catalog(db=db, catalog=catalog)

            # Salvar metadados de parsing no banco para diagnostico
            catalog.parse_metadata = parse_result.to_metadata_dict()
            db.commit()

            products = parse_result.products  # Objetos Product ja persistidos

            logger.info(
                "PARSING %s | %d produtos extraidos | confianca=%s",
                "OK" if parse_result.confidence != ParseConfidence.FAILED else "PARCIAL",
                len(products),
                parse_result.confidence.value,
            )

        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no parsing: {exc}")
            raise self.retry(exc=exc)

        # Parsing FAILED: catalogo nao tem produtos utilizaveis
        if parse_result.confidence == ParseConfidence.FAILED or not products:
            error_detail = (
                "; ".join(parse_result.errors)
                if parse_result.errors
                else "Nenhum produto valido encontrado no catalogo."
            )
            catalog_repo.update_status(
                catalog, CatalogStatus.ERROR,
                f"Parsing falhou: {error_detail}"
            )
            return {
                "status": "parse_failed",
                "catalog_id": catalog_id_str,
                "confidence": parse_result.confidence.value,
            }

        # Parsing PARTIAL: continua mas loga alerta
        if parse_result.confidence == ParseConfidence.PARTIAL:
            logger.warning(
                "Parsing PARCIAL | taxa=%.0f%% | %d/%d produtos | prosseguindo pipeline",
                parse_result.stats.success_rate * 100,
                len(products),
                parse_result.stats.total_rows_scanned,
            )

        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=0)

        # Etapa 2: RESEARCHING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.RESEARCHING)
            processed = market_service.research_catalog(db=db, products=products)
            catalog_repo.update_progress(catalog, total_products=len(products), processed_products=processed)
            logger.info("RESEARCHING OK | %d produtos pesquisados", processed)
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na pesquisa ML: {exc}")
            raise self.retry(exc=exc)

        # Etapa 3: ANALYZING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.ANALYZING)
            db.expire_all()
            from app.repositories.product_repo import ProductRepository
            products = ProductRepository(db).get_by_catalog_with_analyses(catalog.id)
            finance_service.analyze_catalog(db=db, products=products)
            logger.info("ANALYZING OK | analise financeira concluida")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na analise financeira: {exc}")
            raise self.retry(exc=exc)

        # Etapa 4: SCORING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
            db.expire_all()
            products = ProductRepository(db).get_by_catalog_with_analyses(catalog.id)
            strategy_service.score_catalog(db=db, catalog_id=catalog_id, products=products)
            logger.info("SCORING OK | scores calculados")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no scoring: {exc}")
            raise self.retry(exc=exc)

        # Concluido
        catalog_repo.update_status(catalog, CatalogStatus.READY)
        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=len(products))

        logger.info(
            "Pipeline CONCLUIDO | catalog_id=%s | %d produtos analisados",
            catalog_id, len(products)
        )

        return {
            "status": "ready",
            "catalog_id": catalog_id_str,
            "total_products": len(products),
        }

    except Exception:
        # Excecoes de retry ja foram tratadas acima
        raise
    finally:
        db.close()
        # Limpar arquivo temporario se foi criado para este processamento
        if "_temp_file" in dir() and _temp_file is not None:
            import os as _os
            try:
                _os.unlink(_temp_file.name)
            except OSError:
                pass


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="tasks.reprocess_analysis",
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=18000,   # 5h -- mesmo limite que process_catalog
    time_limit=18600,        # 5h10m
)
def reprocess_analysis_task(self: Task, catalog_id_str: str) -> dict:
    """
    Reprocessa apenas as etapas de analise (market -> finance -> score),
    reutilizando os produtos ja extraidos.

    Usado pelo endpoint /reprocess para evitar re-parsear o PDF inteiro.
    Muito mais rapido que process_catalog_task para diagnostico e correcao
    de configuracoes (ex: adicionar credenciais ML apos upload).
    """
    catalog_id = uuid.UUID(catalog_id_str)

    # Imports separados com diagnostico de erro explicito.
    # Se qualquer import falhar (SyntaxError, ImportError), o erro e capturado
    # ANTES de abrir a sessao DB, e escrito diretamente via psycopg2 para o banco.
    # Sem isso, import errors deixam o catalogo preso em PENDING para sempre.
    try:
        from app.db.session import SessionLocal
        from app.models.catalog import CatalogStatus
        from app.models.analysis import FinancialAnalysis, MarketAnalysis, OpportunityScore
        from app.repositories.catalog_repo import CatalogRepository
        from app.repositories.product_repo import ProductRepository
        from app.services import finance_service, market_service, strategy_service
    except Exception as import_exc:
        import traceback
        import psycopg2
        err_msg = f"ImportError na task: {type(import_exc).__name__}: {import_exc}\n{traceback.format_exc()[-500:]}"
        logger.error("IMPORT FALHOU em reprocess_analysis_task: %s", err_msg)
        try:
            _conn = psycopg2.connect(
                host="aws-1-sa-east-1.pooler.supabase.com", port=5432,
                dbname="postgres", user="postgres.gjtzgbvwoiezpiegwvnx",
                password="DZoTXeBpguf7ejiUjr2ztNc5",
            )
            _cur = _conn.cursor()
            _cur.execute(
                "UPDATE catalogs SET status='ERROR', error_message=%s WHERE id=%s",
                (err_msg[:1000], str(catalog_id)),
            )
            _conn.commit()
            _cur.close()
            _conn.close()
        except Exception as db_exc:
            logger.error("Falha ao escrever ImportError no banco: %s", db_exc)
        raise import_exc

    db = SessionLocal()
    try:
        catalog_repo = CatalogRepository(db)
        catalog = catalog_repo.get_by_id(catalog_id)

        if catalog is None:
            logger.error("Catalogo %s nao encontrado", catalog_id)
            return {"status": "not_found", "catalog_id": catalog_id_str}

        # Carregar produtos existentes
        product_repo = ProductRepository(db)
        products = product_repo.get_by_catalog_with_analyses(catalog_id)

        if not products:
            logger.error("Nenhum produto encontrado para catalogo %s", catalog_id)
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, "Nenhum produto encontrado. Faca um novo upload.")
            return {"status": "no_products", "catalog_id": catalog_id_str}

        logger.info(
            "Reprocessando analises | catalog_id=%s | %d produtos existentes",
            catalog_id, len(products)
        )

        # Limpar analises derivadas (Finance + Score), preservar MarketAnalysis existente.
        # Motivo: MarketAnalysis e o passo mais lento (403 x ~20s = 2h+ via Apify).
        # Ao preservar dados ja coletados, o pipeline e resumivel:
        #   - Se a task falhar no produto 200/403, a retentativa retoma do produto 201.
        #   - Se o usuario quiser forcar refresh de precos: implementar /reprocess?force=true (Fase 2).
        product_ids = [p.id for p in products]
        db.query(OpportunityScore).filter(OpportunityScore.product_id.in_(product_ids)).delete(synchronize_session="fetch")
        db.query(FinancialAnalysis).filter(FinancialAnalysis.product_id.in_(product_ids)).delete(synchronize_session="fetch")
        # MarketAnalysis preservada -- reprocessada apenas se ausente (skip_existing=True no market_service)
        db.commit()
        db.expire_all()

        # Etapa 1: RESEARCHING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.RESEARCHING)
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            processed = market_service.research_catalog(db=db, products=products)
            catalog_repo.update_progress(catalog, total_products=len(products), processed_products=processed)
            logger.info("RESEARCHING OK | %d produtos pesquisados", processed)
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na pesquisa ML: {exc}")
            raise self.retry(exc=exc)

        # Etapa 2: ANALYZING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.ANALYZING)
            db.expire_all()
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            finance_service.analyze_catalog(db=db, products =products)
            logger.info("ANALYZING OK | analise financeira concluida")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na analise financeira: {exc}")
            raise self.retry(exc=exc)

        # Etapa 3: SCORING
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
            db.expire_all()
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            strategy_service.score_catalog(db=db, catalog_id=catalog_id, products=products)
            logger.info("SCORING OK | scores calculados")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no scoring: {exc}")
            raise self.retry(exc=exc)

        # Concluido
        catalog_repo.update_status(catalog, CatalogStatus.READY)
        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=len(products))

        logger.info(
            "Reprocessamento CONCLUIDO | catalog_id=%s | %d produtos analisados",
            catalog_id, len(products)
        )

        return {
            "status": "ready",
            "catalog_id": catalog_id_str,
            "total_products": len(products),
        }

    except Exception:
        raise
    finally:
        db.close()
