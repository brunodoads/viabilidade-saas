from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    Em caso de erro: qualquer estado → ERROR
    """

    PENDING = "PENDING"          # Upload recebido, aguardando processamento
    PARSING = "PARSING"          # Scout service extraindo produtos
    RESEARCHING = "RESEARCHING"  # Market service buscando no ML
    ANALYZING = "ANALYZING"      # Finance service calculando margens
    SCORING = "SCORING"          # Strategy service gerando scores
    READY = "READY"              # Pipeline concluído, dashboard disponível
    ERROR = "ERROR"              # Falha em alguma etapa (ver error_message)


class Catalog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Catálogo enviado por um usuário para análise.
    Cada catálogo dispara um pipeline completo de processamento.
    """

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
        comment="Resultado do parsing: confiança, estatísticas, warnings (ParseResult.to_metadata_dict())",
    )

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
            
