from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FileType(str, enum.Enum):
    """Tipos de arquivo aceitos no upload de catálogo."""
    PDF = "PDF"
    XLSX = "XLSX"
    CSV = "CSV"


class CatalogStatus(str, enum.Enum):
    """
    Estados do pipeline de processamento de um catálogo.
    Fluxo: PENDING → PARSING → RESEARCHING → ANALYZING → SCORING → READY
    """
    PENDING = "PENDING"
    PARSING = "PARSING"
    RESEARCHING = "RESEARCHING"
    ANALYZING = "ANALYZING"
    SCORING = "SCORING"
    READY = "READY"
    ERROR = "ERROR"


class Catalog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Catálogo enviado por um usuário para análise."""
    __tablename__ = "catalogs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Dono do catálogo — escopo de segurança MVP",
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Nome original do arquivo enviado pelo usuário",
    )
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Caminho absoluto do arquivo salvo em disco",
    )
    file_type: Mapped[FileType] = mapped_column(
        SAEnum(FileType, name="filetype_enum", create_type=True),
        nullable=False,
        comment="Tipo do arquivo detectado pelo MIME",
    )
    status: Mapped[CatalogStatus] = mapped_column(
        SAEnum(CatalogStatus, name="catalogstatus_enum", create_type=True),
        nullable=False,
        default=CatalogStatus.PENDING,
        index=True,
        comment="Estado atual no pipeline de processamento",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Mensagem de erro quando status=ERROR",
    )
    total_products: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Total de produtos extraídos do catálogo",
    )
    processed_products: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Produtos já processados pelo pipeline (progresso)",
    )
    parse_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Resultado do parsing: confiança, estatísticas, warnings",
    )
    # DEFERRED: não carregado em queries default — apenas quando acessado explicitamente.
    # Evita carregar ~24 MB de PDF binário em toda listagem de catálogos, que causava
    # timeout de 30s no /api/catalogs/ (7 catálogos × 24 MB = ~170 MB por request).
    # O worker Celery acessa dentro da sessão aberta → SQLAlchemy faz lazy SELECT automaticamente.
    file_content: Mapped[bytes | None] = deferred(mapped_column(
        LargeBinary,
        nullable=True,
        comment=(
            "Conteúdo binário do arquivo — armazenado no DB para que o worker "
            "Celery (container separado) acesse sem precisar de filesystem compartilhado. "
            "MVP: BYTEA no PostgreSQL. Fase 2: migrar para S3/Supabase Storage. "
            "DEFERRED: não carregado por default."
        ),
    ))

    # Relacionamentos
    user: Mapped[User] = relationship(  # type: ignore[name-defined]
        "User",
        back_populates="catalogs",
    )
    products: Mapped[list[Product]] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="catalog",
        cascade="all, delete-orphan",
    )

    @property
    def progress_pct(self) -> float | None:
        """Percentual de progresso (0-100), ou None se não iniciado."""
        if self.total_products and self.processed_products is not None:
            return round((self.processed_products / self.total_products) * 100, 1)
        return None

    def __repr__(self) -> str:
        return f"<Catalog id={self.id} status={self.status} file={self.original_filename}>"
