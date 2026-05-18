"""
Celery Tasks â Pipeline de Processamento de CatÃ¡logos.
"""

import logging
import uuid

from celery import Task

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class PipelineTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Task %s falhou | task_id=%s | erro=%s", self.name, task_id, str(exc), exc_info=True)


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="tasks.process_catalog",
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=1800,
    time_limit=2100,
)
def process_catalog_task(self: Task, catalog_id_str: str) -> dict:
    catalog_id = uuid.UUID(catalog_id_str)

    # Import local para evitar circular import com Celery
    from app.db.session import SessionLocal
    db = SessionLocal()
    _temp_file = None

    try:
        from app.models.catalog import CatalogStatus
        from app.repositories.catalog_repo import CatalogRepository
        from app.services import finance_service, market_service, scout_service, strategy_service

        catalog_repo = CatalogRepository(db)
        catalog = catalog_repo.get_by_id(catalog_id)

        if catalog is None:
            logger.error("CatÃ¡logo %s nÃ£o encontrado", catalog_id)
            return {"status": "not_found", "catalog_id": catalog_id_str}

        logger.info("Iniciando pipeline | catalog_id=%s | arquivo=%s", catalog_id, catalog.original_filename)

        # ââ Materializar arquivo em disco (cross-container) ââââââââââââââââââ
        # O backend salva em /app/uploads/ do SEU container.
        # O worker roda em container SEPARADO â filesystems independentes.
        # SoluÃ§Ã£o MVP: file_content estÃ¡ no DB. Se arquivo nÃ£o existir localmente,
        # escrevemos do DB para um temp file antes do parsing.
        import tempfile
        from pathlib import Path as _Path

        _file_path = _Path(catalog.file_path)

        if not _file_path.exists() and catalog.file_content:
            suffix = _file_path.suffix or ".bin"
            _temp_file = tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False, prefix="catalog_"
            )
            _temp_file.write(catalog.file_content)
            _temp_file.flush()
            _temp_file.close()
            catalog.file_path = _temp_file.name
            logger.info("Arquivo materializado do DB â %s | catalog_id=%s", _temp_file.name, catalog_id)
        elif not _file_path.exists() and not catalog.file_content:
            raise FileNotFoundError(
                f"Arquivo nÃ£o encontrado em disco ({catalog.file_path}) e "
                f"file_content nÃ£o estÃ¡ no banco. Re-faÃ§a o upload."
            )

        # ââ Etapa 1: PARSING ââââââââââââââââââââââââââââââââââââââââââââââââ
        from app.services.parse_result import ParseConfidence

        try:
            catalog_repo.update_status(catalog, CatalogStatus.PARSING)
            parse_result = scout_service.parse_catalog(db=db, catalog=catalog)

            catalog.parse_metadata = parse_result.to_metadata_dict()
            db.commit()

            products = parse_result.products

            logger.info(
                "PARSING %s | %d produtos | confianÃ§a=%s",
                "OK" if parse_result.confidence != ParseConfidence.FAILED else "PARCIAL",
                len(products), parse_result.confidence.value,
            )

        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no parsing: {exc}")
            raise self.retry(exc=exc)

        if parse_result.confidence == ParseConfidence.FAILED or not products:
            error_detail = (
                "; ".join(parse_result.errors) if parse_result.errors
                else "Nenhum produto vÃ¡lido encontrado no catÃ¡logo."
            )
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Parsing falhou: {error_detail}")
            return {"status": "parse_failed", "catalog_id": catalog_id_str, "confidence": parse_result.confidence.value}

        if parse_result.confidence == ParseConfidence.PARTIAL:
            logger.warning(
                "Parsing PARCIAL | taxa=%.0f%% | %d/%d produtos | prosseguindo",
                parse_result.stats.success_rate * 100, len(products), parse_result.stats.total_rows_scanned,
            )

        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=0)

        # ââ Etapa 2: RESEARCHING âââââââââââââââââââââââââââââââââââââââââââââ
        try:
            catalog_repo.update_status(catalog, CatalogStatus.RESEARCHING)
            processed = market_service.research_catalog(db=db, products=products)
            catalog_repo.update_progress(catalog, total_products=len(products), processed_products=processed)
            logger.info("RESEARCHING OK | %d produtos pesquisados", processed)
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na pesquisa ML: {exc}")
            raise self.retry(exc=exc)

        # ââ Etapa 3: ANALYZING âââââââââââââââââââââââââââââââââââââââââââââââ
        try:
            catalog_repo.update_status(catalog, CatalogStatus.ANALYZING)
            finance_service.analyze_catalog(db=db, products=products)
            logger.info("ANALYZING OK")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro na anÃ¡lise financeira: {exc}")
            raise self.retry(exc=exc)

        # ââ Etapa 4: SCORING âââââââââââââââââââââââââââââââââââââââââââââââââ
        try:
            catalog_repo.update_status(catalog, CatalogStatus.SCORING)
            strategy_service.score_catalog(db=db, catalog_id=catalog_id, products=products)
            logger.info("SCORING OK")
        except Exception as exc:
            catalog_repo.update_status(catalog, CatalogStatus.ERROR, f"Erro no scoring: {exc}")
            raise self.retry(exc=exc)

        # ââ ConcluÃ­do ââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
        catalog_repo.update_status(catalog, CatalogStatus.READY)
        catalog_repo.update_progress(catalog, total_products=len(products), processed_products=len(products))

        logger.info("Pipeline CONCLUÃDO | catalog_id=%s | %d produtos", catalog_id, len(products))

        return {"status": "ready", "catalog_id": catalog_id_str, "total_products": len(products)}

    except Exception as outer_exc:
        # Captura erros fora do fluxo esperado (ex: falha de import)
        # Tenta marcar como ERROR para o usuario nao ficar em PENDING
        try:
            from app.models.catalog import CatalogStatus
            from app.repositories.catalog_repo import CatalogRepository
            _repo = CatalogRepository(db)
            _cat = _repo.get_by_id(catalog_id)
            if _cat:
                _repo.update_status(_cat, CatalogStatus.ERROR, f"Erro interno: {outer_exc}")
        except Exception:
            pass
        raise
    finally:
        db.close()
        # Limpar arquivo temporÃ¡rio se foi criado neste processamento
        if _temp_file is not None:
            import os as _os
            try:
                _os.unlink(_temp_file.name)
            except OSError:
                pass
