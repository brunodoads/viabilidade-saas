"""
MarketListing — Anúncios individuais do Mercado Livre coletados por produto.

Armazena os top N anúncios qualificados de cada busca para que o frontend
possa mostrar links diretos do ML, thumbnails e comparação de preços.

Relacionamento: Product 1→N MarketListing
Cada re-análise substitui os listings existentes (upsert por product_id + rank_position).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class MarketListing(Base, UUIDPrimaryKeyMixin):
    """
    Anúncio individual do Mercado Livre associado a um produto do catálogo.

    Salvo durante a fase de pesquisa de mercado (Mercado Agent).
    Permite que o usuário veja os links reais que embasaram a análise.
    """

    __tablename__ = "market_listings"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Posição no ranking interno (1 = melhor match) ─────────────────────────
    rank_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Posição entre os top listings (1, 2, 3, 4, 5)",
    )

    # ── Dados do anúncio ML ───────────────────────────────────────────────────
    item_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="ID do item no ML (ex: MLB4290861023)",
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Título do anúncio no ML",
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Preço de venda no ML em R$",
    )
    sold_quantity: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Unidades vendidas — enriquecido via ML Items API",
    )
    permalink: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="URL completa do anúncio no ML",
    )
    thumbnail: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="URL da imagem miniatura do anúncio",
    )
    match_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
        comment="Score de matching do produto com o anúncio (0.000–1.000)",
    )

    # ── Frete e taxa real (enriquecidos via ML APIs) ──────────────────────────
    free_shipping: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="True se o vendedor oferece frete grátis neste anúncio",
    )
    logistic_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Tipo de logística: fulfillment, drop_off, self_service, not_specified",
    )
    ml_fee_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Taxa real do ML em % para esta categoria/preço (via Listing Prices API)",
    )
    category_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="ID da categoria ML (ex: MLB1648) — usado para calcular taxa real",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relacionamento
    product: Mapped["Product"] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="market_listings",
    )

    def __repr__(self) -> str:
        return (
            f"<MarketListing product_id={self.product_id} "
            f"rank={self.rank_position} item={self.item_id} price=R${self.price}>"
        )
