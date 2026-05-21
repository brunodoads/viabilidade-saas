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
    # 403 produtos × ~20s/Apify = ~2.25h. Margem de 2x para catálogos grandes.
    soft_time_limit=18000,   # 5h — timeout soft (lança SoftTimeLimitExceeded)
    time_limit=18600,        # 5h10m — timeout hard (mata o processo)
)
def process_catalog_task(self: Task, catalog_id_str: str) -> dict:
    """
    Task principal: orquestra o pipeline completo para um catálogo.

    Etapas:
    1. PARSING    — scout_service.parse_catalog()
    2. RESEARCHING — market_service.research_catalog()
    3. ANALYZING  — finance_service.analyze_catalog()
    4. SCORING    — strategy_service.score_catalog()
    5. READY      — pipeline concluído

    Em caso de erro:
    - Atualiza catalog.status = ERROR com mensagem
    - Salva no banco para o usuário ver via polling
    - Re-lança exceção para Celery registrar o failure
    """
    catalog_id = uuid.UUID(catalog_id_str)

    # Import local para evitar inicialização do Celery importar app antes de configurar
    from app.db.session import SessionLocal
    from app.models.catalog import CatalogStatus
    from app.repositories.catalog_repo import CatalogRepository
    from app.services import finance_service, market_service, scout_service, strategy_service

    db = SessionLocal()
    try:
        catalog_repo = CatalogRepository(db)
        catalog = catalog_repo.get_by_id(catalog_id)

        if catalog is None:
            logger.error("Catálogo %s não encontrado", catalog_id)
            return {"status": "not_found", "catalog_id": catalog_id_str}

        logger.info("Iniciando pipeline | catalog_id=%s | arquivo=%s", catalog_id, catalog.original_filename)

        # ── Materializar arquivo em disco (cross-container) ──────────────────
        # O backend salva o arquivo no SEU container (/app/uploads/).
        # O worker roda em container SEPARADO — sem filesystem compartilhado.
        # Solução MVP: file_content está no PostgreSQL. Se o arquivo não existir
        # localmente, escrevemos do DB para um path temporário antes do parsing.
        import tempfile
        from pathlib import Path as _Path

        _file_path = _Path(catalog.file_path)
        _temp_file = None

        if not _file_path.exists() and catalog.file_content:
            # Criar arquivo temporário com a extensão original
            suffix = _file_path.suffix or ".bin"
            _temp_file = tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False, prefix="catalog_"
            )
            _temp_file.write(catalog.file_content)
            _temp_file.flush()
            _temp_file.close()
            catalog.file_path = _temp_file.name
            logger.info(
                "Arquivo materializado do DB → %s | catalog_id=%s",
                _temp_file.name, catalog_id
            )

        # ── Etapa 1: PARSING ────────────────────────────────────────────────
        from app.services.parse_result import ParseConfidence

        try:
            catalog_repo.update_status(catalog, CatalogStatus.PARSING)
            parse_result = scout_service.parse_catalog(db=db, catalog=catalog)

            # Salvar metadados de parsing no banco para diagnóstico
            catalog.parse_metadata = parse_result.to_metadata_dict()
            db.commit()

            products = parse_result.products  # Objetos Product já persistidos

            logger.info(
                "PARSING %s | %d produtos extraídos | confiança=%s",
                "OK" if parse_result.confidence != ParseConfidence.FAILED else "PARCIAL",
                len(products),
                parse_result.confidence.value,
            )

        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no parsing: {exc}")
            raise self.retry(exc=exc)

        # Parsing FAILED: catálogo não tem produtos utilizáveis
        if parse_result.confidence == ParseConfidence.FAILED or not products:
            error_detail = (
                "; ".join(parse_result.errors)
                if parse_result.errors
                else "Nenhum produto válido encontrado no catálogo."
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

        # ── Etapa 2: RESEARCHING ─────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.RESEARCHING)
            processed = market_service.research_catalog(db=db, products=products)
            catalog_repo.update_progress(catalog, total_products=len(products), processed_products=processed)
            logger.info("RESEARCHING OK | %d produtos pesquisados", processed)
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na pesquisa ML: {exc}")
            raise self.retry(exc=exc)

        # ── Etapa 3: ANALYZING ───────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.ANALYZING)
            # Expire all cached attributes so finance_service vê os market_analysis
            # criados pela etapa anterior (SQLAlchemy pode ter em memória objetos stale)
            db.expire_all()
            # Recarregar produtos com market_analysis via eager loading
            from app.repositories.product_repo import ProductRepository
            products = ProductRepository(db).get_by_catalog_with_analyses(catalog.id)
            finance_service.analyze_catalog(db=db, products=products)
            logger.info("ANALYZING OK | análise financeira concluída")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na análise financeira: {exc}")
            raise self.retry(exc=exc)

        # ── Etapa 4: SCORING ─────────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
            # Recarregar produtos com financial_analysis para o scoring
            db.expire_all()
            products = ProductRepository(db).get_by_catalog_with_analyses(catalog.id)
            strategy_service.score_catalog(db=db, catalog_id=catalog_id, products=products)
            logger.info("SCORING OK | scores calculados")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no scoring: {exc}")
            raise self.retry(exc=exc)

        # ── Concluído ────────────────────────────────────────────────────────
        catalog_repo.update_status(catalog, CatalogStatus.READY)
        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=len(products))

        logger.info(
            "Pipeline CONCLUÍDO | catalog_id=%s | %d produtos analisados",
            catalog_id, len(products)
        )

        return {
            "status": "ready",
            "catalog_id": catalog_id_str,
            "total_products": len(products),
        }

    except Exception:
        # Exceções de retry já foram tratadas acima
        # Isso captura erros fora do fluxo esperado
        raise
    finally:
        db.close()
        # Limpar arquivo temporário se foi criado para este processamento
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
    soft_time_limit=18000,   # 5h — mesmo limite que process_catalog
    time_limit=18600,        # 5h10m
)
def reprocess_analysis_task(self: Task, catalog_id_str: str) -> dict:
    """
    Reprocessa apenas as etapas de análise (market → finance → score),
    reutilizando os produtos já extraídos.

    Usado pelo endpoint /reprocess para evitar re-parsear o PDF inteiro.
    Muito mais rápido que process_catalog_task para diagnóstico e correção
    de configurações (ex: adicionar credenciais ML após upload).
    """
    catalog_id = uuid.UUID(catalog_id_str)

    from app.db.session import SessionLocal
    from app.models.catalog import CatalogStatus
    from app.models.analysis import FinancialAnalysis, MarketAnalysis, OpportunityScore
    from app.repositories.catalog_repo import CatalogRepository
    from app.repositories.product_repo import ProductRepository
    from app.services import finance_service, market_service, strategy_service

    db = SessionLocal()
    try:
        catalog_repo = CatalogRepository(db)
        catalog = catalog_repo.get_by_id(catalog_id)

        if catalog is None:
            logger.error("Catálogo %s não encontrado", catalog_id)
            return {"status": "not_found", "catalog_id": catalog_id_str}

        # Carregar produtos existentes
        product_repo = ProductRepository(db)
        products = product_repo.get_by_catalog_with_analyses(catalog_id)

        if not products:
            logger.error("Nenhum produto encontrado para catálogo %s", catalog_id)
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, "Nenhum produto encontrado. Faça um novo upload.")
            return {"status": "no_products", "catalog_id": catalog_id_str}

        logger.info(
            "Reprocessando análises | catalog_id=%s | %d produtos existentes",
            catalog_id, len(products)
        )

        # Limpar análises derivadas (Finance + Score), preservar MarketAnalysis existente.
        # Motivo: MarketAnalysis é o passo mais lento (403 × ~20s = 2h+ via Apify).
        # Ao preservar dados já coletados, o pipeline é resumível:
        #   - Se a task falhar no produto 200/403, a retentativa retoma do produto 201.
        #   - Se o usuário quiser forçar refresh de preços: implementar /reprocess?force=true (Fase 2).
        product_ids = [p.id for p in products]
        db.query(OpportunityScore).filter(OpportunityScore.product_id.in_(product_ids)).delete(synchronize_session="fetch")
        db.query(FinancialAnalysis).filter(FinancialAnalysis.product_id.in_(product_ids)).delete(synchronize_session="fetch")
        # MarketAnalysis preservada — reprocessada apenas se ausente (skip_existing=True no market_service)
        db.commit()
        db.expire_all()

        # ── Etapa 1: RESEARCHING ─────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.RESEARCHING)
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            processed = market_service.research_catalog(db=db, products=products)
            catalog_repo.update_progress(catalog, total_products=len(products), processed_products=processed)
            logger.info("RESEARCHING OK | %d produtos pesquisados", processed)
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na pesquisa ML: {exc}")
            raise self.retry(exc=exc)

        # ── Etapa 2: ANALYZING ───────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.ANALYZING)
            db.expire_all()
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            finance_service.analyze_catalog(db=db, products=products)
            logger.info("ANALYZING OK | análise financeira concluída")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na análise financeira: {exc}")
            raise self.retry(exc=exc)

        # ── Etapa 3: SCORING ─────────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
            db.expire_all()
            products = product_repo.get_by_catalog_with_analyses(catalog_id)
            strategy_service.score_catalog(db=db, catalog_id=catalog_id, products=products)
            logger.info("SCORING OK | scores calculados")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no scoring: {exc}")
            raise self.retry(exc=exc)

        # ── Concluído ────────────────────────────────────────────────────────
        catalog_repo.update_status(catalog, CatalogStatus.READY)
        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=len(products))

        logger.info(
            "Reprocessamento CONCLUÍDO | catalog_id=%s | %d produtos analisados",
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
