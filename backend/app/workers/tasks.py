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
    soft_time_limit=1800,    # 30min — timeout soft (lança SoftTimeLimitExceeded)
    time_limit=2100,         # 35min — timeout hard (mata o processo)
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
            finance_service.analyze_catalog(db=db, products=products)
            logger.info("ANALYZING OK | análise financeira concluída")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na análise financeira: {exc}")
            raise self.retry(exc=exc)

        # ── Etapa 4: SCORING ─────────────────────────────────────────────────
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
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
