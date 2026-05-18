from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Produto extraído de um catálogo.

    Armazena tanto o nome original (raw_name) quanto o nome normalizado
    pela IA (normalized_name). O normalized_name é usado para busca no ML.
    """

    __tablename__ = "products"

    catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("catalogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Desnormalizado do catalog para filtros diretos por usuário",
    )
    raw_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Nome exatamente como veio no catálogo (nunca alterar)",
    )
    normalized_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Nome normalizado pela Claude API — usado para busca no ML",
    )
    sku: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Código SKU do produto no catálogo do fornecedor",
    )
    category: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Categoria do produto (extraída do catálogo ou inferida pela IA)",
    )
    supplier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Nome do fornecedor/importadora",
    )
    cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Custo unitário de compra em BRL",
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="BRL",
        comment="Moeda do custo (padrão BRL)",
    )

    # Relacionamentos
    catalog: Mapped[Catalog] = relationship(  # type: ignore[name-defined]
        "Catalog",
        back_populates="products",
    )
    market_analysis: Mapped[MarketAnalysis | None] = relationship(  # type: ignore[name-defined]
        "MarketAnalysis",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
    )
    financial_analysis: Mapped[FinancialAnalysis | None] = relationship(  # type: ignore[name-defined]
        "FinancialAnalysis",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
    )
    opportunity_score: Mapped[OpportunityScore | None] = relationship(  # type: ignore[name-defined]
        "OpportunityScore",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def search_name(self) -> str:
        """Nome a usar na busca do ML: normalizado se disponível, raw caso contrário."""
        return self.normalized_name or self.raw_name

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.search_name} cost={self.cost}>"
