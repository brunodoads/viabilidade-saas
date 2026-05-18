import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.analysis import Recommendation


class MarketDataResponse(BaseModel):
    """Dados de mercado do ML para um produto."""

    model_config = {"from_attributes": True}

    avg_price: Decimal
    min_price: Decimal
    max_price: Decimal
    total_sellers: int
    listings_above_threshold: int


class FinancialDataResponse(BaseModel):
    """Dados financeiros calculados para um produto."""

    model_config = {"from_attributes": True}

    cost: Decimal
    avg_market_price: Decimal
    marketplace_fee_pct: Decimal
    gross_margin: Decimal
    gross_margin_pct: Decimal
    break_even_price: Decimal
    is_viable: bool


class OpportunityResponse(BaseModel):
    """
    Oportunidade completa — produto + mercado + financeiro + score.
    Retornada no dashboard principal.
    """

    model_config = {"from_attributes": True}

    # Produto
    product_id: uuid.UUID
    raw_name: str
    normalized_name: str | None
    sku: str | None
    category: str | None
    cost: Decimal

    # Score
    final_score: Decimal = Field(description="Score 0-100")
    rank: int = Field(description="Posição no ranking (1 = melhor)")
    recommendation: Recommendation
    demand_score: Decimal
    margin_score: Decimal
    competition_score: Decimal

    # Mercado
    market: MarketDataResponse | None

    # Financeiro
    financial: FinancialDataResponse | None


class OpportunityListResponse(BaseModel):
    """Lista paginada de oportunidades de um catálogo."""

    catalog_id: uuid.UUID
    total: int
    items: list[OpportunityResponse]
