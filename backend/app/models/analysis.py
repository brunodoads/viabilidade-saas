from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class MarketAnalysis(Base, UUIDPrimaryKeyMixin):
    """Dados de mercado coletados do Mercado Livre para um produto."""

    __tablename__ = "market_analyses"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    avg_sold_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_sold_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_listings_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    listings_above_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    avg_match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    # Taxa ML real e frete -- enriquecidos via Listing Prices API + ML Items API
    avg_ml_fee_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Taxa media real do ML em % via Listing Prices API",
    )
    free_shipping_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Percentual de anuncios com frete gratis (0-100)",
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    product: Mapped["Product"] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="market_analysis",
    )

    def __repr__(self) -> str:
        return (
            f"<MarketAnalysis product_id={self.product_id} "
            f"avg_price={self.avg_price} sellers={self.total_sellers}>"
        )


class FinancialAnalysis(Base, UUIDPrimaryKeyMixin):
    """Analise financeira calculada para um produto."""

    __tablename__ = "financial_analyses"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    avg_market_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    marketplace_fee_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("15.00")
    )

    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    ml_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gross_margin_pct: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    break_even_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_safety_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_viable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Fase 2 slots -- NULL no MVP, preenchidos quando disponíveis
    ads_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    return_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    packaging_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fulfillment_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    tax_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    min_price_for_target_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    product: Mapped["Product"] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="financial_analysis",
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialAnalysis product_id={self.product_id} "
            f"margin={self.gross_margin_pct}% viable={self.is_viable}>"
        )


class Recommendation(str, enum.Enum):
    EXCELENTE = "EXCELENTE"
    BOA = "BOA"
    ARRISCADA = "ARRISCADA"
    EVITAR = "EVITAR"


class OpportunityScore(Base, UUIDPrimaryKeyMixin):
    """Score estrategico final calculado para uma oportunidade."""

    __tablename__ = "opportunity_scores"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    demand_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    margin_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    competition_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("100.00")
    )

    final_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[Recommendation] = mapped_column(
        SAEnum(Recommendation, name="recommendation_enum", create_type=False),
        nullable=False,
    )
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    product: Mapped["Product"] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="opportunity_score",
    )

    def __repr__(self) -> str:
        return (
            f"<OpportunityScore product_id={self.product_id} "
            f"score={self.final_score} rank=#{self.rank} rec={self.recommendation}>"
        )
