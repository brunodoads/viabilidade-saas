import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.db.session import get_db
from app.models.catalog import CatalogStatus, FileType
from app.models.user import User
from app.api.deps import get_current_user
from app.repositories.catalog_repo import CatalogRepository
from app.schemas.catalog import CatalogListResponse, CatalogStatusResponse, CatalogUploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalogs", tags=["Catálogos"])

ALLOWED_EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".xlsx": FileType.XLSX,
    ".xls": FileType.XLSX,
    ".csv": FileType.CSV,
}

ALLOWED_MIME_TYPES: set[str] = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
    "application/octet-stream",
}


def _detect_file_type(filename: str, content_type: str | None) -> FileType:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise BadRequestException(
            f"Formato não suportado: '{suffix}'. "
            f"Use: {', '.join(ALLOWED_EXTENSIONS.keys())}"
        )
    return ALLOWED_EXTENSIONS[suffix]


def _validate_file_size(file_size: int) -> None:
    if file_size > settings.max_upload_size_bytes:
        raise BadRequestException(
            f"Arquivo muito grande. Máximo: {settings.MAX_UPLOAD_SIZE_MB}MB"
        )


@router.post(
    "/upload",
    response_model=CatalogUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enviar catálogo para análise",
)
def upload_catalog(
    file: UploadFile = File(..., description="Catálogo do fornecedor (PDF, XLSX ou CSV)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CatalogUploadResponse:
    """
    Recebe catálogo, salva em disco, cria registro no banco e enfileira no Celery.
    O upload sempre retorna 202 mesmo se o broker estiver indisponível.
    """
    filename = file.filename or "unknown"
    file_type = _detect_file_type(filename, file.content_type)

    content = file.file.read()
    _validate_file_size(len(content))

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = upload_dir / unique_filename
    file_path.write_bytes(content)

    repo = CatalogRepository(db)
    catalog = repo.create_catalog(
        user_id=current_user.id,
        original_filename=filename,
        file_path=str(file_path.absolute()),
        file_type=file_type,
    )

    # Disparar pipeline assíncrono — resiliente a broker indisponível
    try:
        from app.workers.tasks import process_catalog_task
        process_catalog_task.delay(str(catalog.id))
    except Exception as exc:
        logger.warning(
            "Celery broker indisponível — catálogo %s ficará PENDING. Erro: %s",
            catalog.id,
            str(exc),
        )

    return CatalogUploadResponse.model_validate(catalog)


@router.get(
    "/",
    response_model=list[CatalogListResponse],
    summary="Listar catálogos do usuário",
)
def list_catalogs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CatalogListResponse]:
    repo = CatalogRepository(db)
    catalogs = repo.get_by_user(user_id=current_user.id)
    return [CatalogListResponse.model_validate(c) for c in catalogs]


@router.get(
    "/{catalog_id}/status",
    response_model=CatalogStatusResponse,
    summary="Verificar status do processamento",
)
def get_catalog_status(
    catalog_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CatalogStatusResponse:
    repo = CatalogRepository(db)
    catalog = repo.get_by_id_and_user(catalog_id=catalog_id, user_id=current_user.id)

    if catalog is None:
        raise NotFoundException("Catálogo")

    return CatalogStatusResponse.model_validate(catalog)
