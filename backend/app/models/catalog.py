from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FileType(str, enum.Enum):
    PDF = "PDF"
    XLSX = "XLSX"
    CSV = "CSV"


class CatalogStatus(str, enum.Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    RESEARCHING = "RESEARCHING"
    ANALYZING = "ANALYZING"
    SCORING = "SCORING"
    READY = "READY"
    ERROR = "ERROR"


class Catalog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "catalogs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[FileType] = mapped_column(
        SAEnum(FileType, name="filetype_enum", create_type=True), nullable=False,
    )
    status: Mapped[CatalogStatus] = mapped_column(
        SAEnum(CatalogStatus, name="catalogstatus_enum", create_type=True),
        nullable=False, default=CatalogStatus.PENDING, index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_products: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_products: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    file_content: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True,
        comment="Binário do arquivo no DB — worker cross-container. MVP: BYTEA. Fase 2: S3.",
    )

    user: Mapped[User] = relationship("User", back_populates="catalogs")  # type: ignore[name-defined]
    products: Mapped[list[Product]] = relationship(  # type: ignore[name-defined]
        "Product", back_populates="catalog", cascade="all, delete-orphan",
    )

    @property
    def progress_pct(self) -> float | None:
        if self.total_products and self.processed_products is not None:
            return round((self.processed_products / self.total_products) * 100, 1)
        return None

    def __repr__(self) -> str:
        return f"<Catalog id={self.id} status={self.status} file={self.original_filename}>"
