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

router = APIRouter(prefix="/catalogs", tags=["Catálogos"])

# Mapeamento extensão → FileType
ALLOWED_EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".xlsx": FileType.XLSX,
    ".xls": FileType.XLSX,
    ".csv": FileType.CSV,
}

# MIME types aceitos por extensão (validação dupla — não confia só na extensão)
ALLOWED_MIME_TYPES: set[str] = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",  # Alguns CSVs vêm como text/plain
    "application/octet-stream",  # Fallback genérico de alguns browsers
}


def _detect_file_type(filename: str, content_type: str | None) -> FileType:
    """
    Detecta o tipo de arquivo pela extensão.
    Validação adicional por MIME type como segundo fator.
    """
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
    description=(
        "Recebe um arquivo PDF, XLSX ou CSV e enfileira para processamento. "
        "Retorna imediatamente com o catalog_id para polling de status."
    ),
)
def upload_catalog(
    file: UploadFile = File(..., description="Catálogo do fornecedor (PDF, XLSX ou CSV)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CatalogUploadResponse:
    """
    Endpoint de upload de catálogo.

    Fluxo:
    1. Valida extensão e tamanho
    2. Salva arquivo em disco
    3. Cria registro no banco com status PENDING
    4. Dispara task Celery assíncrona
    5. Retorna catalog_id para polling
    """
    # Validações
    filename = file.filename or "unknown"
    file_type = _detect_file_type(filename, file.content_type)

    # Ler conteúdo e validar tamanho
    content = file.file.read()
    _validate_file_size(len(content))

    # Salvar arquivo com nome único para evitar colisões
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = upload_dir / unique_filename

    file_path.write_bytes(content)

    # Criar registro no banco
    repo = CatalogRepository(db)
    catalog = repo.create_catalog(
        user_id=current_user.id,
        original_filename=filename,
        file_path=str(file_path.absolute()),
        file_type=file_type,
    )

    # Disparar pipeline assíncrono
    # Import local para evitar circular import com Celery
    from app.workers.tasks import process_catalog_task

    process_catalog_task.delay(str(catalog.id))

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
    """Retorna todos os catálogos do usuário autenticado, ordenados por data."""
    repo = CatalogRepository(db)
    catalogs = repo.get_by_user(user_id=current_user.id)
    return [CatalogListResponse.model_validate(c) for c in catalogs]


@router.get(
    "/{catalog_id}/status",
    response_model=CatalogStatusResponse,
    summary="Verificar status do processamento",
    description=(
        "Polling endpoint — consulte a cada 5s enquanto status != READY ou ERROR. "
        "Retorna progresso e estado atual do pipeline."
    ),
)
def get_catalog_status(
    catalog_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CatalogStatusResponse:
    """
    Retorna o estado atual do pipeline para um catálogo.
    Usado pelo frontend em polling a cada 5s.
    """
    repo = CatalogRepository(db)
    catalog = repo.get_by_id_and_user(catalog_id=catalog_id, user_id=current_user.id)

    if catalog is None:
        raise NotFoundException("Catálogo")

    return CatalogStatusResponse.model_validate(catalog)
