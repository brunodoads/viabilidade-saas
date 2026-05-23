import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.analysis import Recommendation


class MarketListingResponse(BaseModel):
    """Anuncio individual do ML -- link direto para o produto pesquisado."""

    model_config = {"from_attributes": True}

    rank_position: int
    item_id: str
    title: str
    price: Decimal
    sold_quantity: int | None
    permalink: str | None
    thumbnail: str | None
    match_confidence: Decimal | None
    free_shipping: bool | None = None
    logistic_type: str | None = None
    ml_fee_pct: Decimal | None = None


class MarketDataResponse(BaseModel):
    """Dados de mercado do ML para um produto."""

    model_config = {"from_attributes": True}

    avg_price: Decimal
    min_price: Decimal
    max_price: Decimal
    total_sellers: int
    listings_above_threshold: int
    avg_ml_fee_pct: Decimal | None = None
    free_shipping_pct: Decimal | None = None
    listings: list[MarketListingResponse] = Field(default_factory=list)


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
    net_margin: Decimal | None = None
    net_margin_pct: Decimal | None = None
    min_price_for_target_margin: Decimal | None = None


class OpportunityResponse(BaseModel):
    """
    Oportunidade completa -- produto + mercado + financeiro + score.
    Retornada no dashboard principal.
    """

    model_config = {"from_attributes": True}

    product_id: uuid.UUID
    raw_name: str
    normalized_name: str | None
    sku: str | None
    category: str | None
    cost: Decimal

    final_score: Decimal = Field(description="Score 0-100")
    rank: int = Field(description="Posicao no ranking (1 = melhor)")
    recommendation: Recommendation
    demand_score: Decimal
    margin_score: Decimal
    competition_score: Decimal

    market: MarketDataResponse | None = None
    financial: FinancialDataResponse | None = None


class OpportunityListResponse(BaseModel):
    """Lista de oportunidades de um catalogo."""

    catalog_id: uuid.UUID
    total: int
    items: list[OpportunityResponse]
