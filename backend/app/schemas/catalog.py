import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.catalog import CatalogStatus, FileType


class CatalogUploadResponse(BaseModel):
    """Retorno imediato após upload — antes do processamento iniciar."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    original_filename: str
    file_type: FileType
    status: CatalogStatus
    created_at: datetime


class CatalogStatusResponse(BaseModel):
    """Status de processamento de um catálogo — usado no polling do frontend."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    original_filename: str
    status: CatalogStatus
    error_message: str | None
    total_products: int | None
    processed_products: int | None
    progress_pct: float | None
    created_at: datetime
    updated_at: datetime


class CatalogListResponse(BaseModel):
    """Catálogo na listagem do usuário."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    original_filename: str
    file_type: FileType
    status: CatalogStatus
    total_products: int | None
    created_at: datetime
