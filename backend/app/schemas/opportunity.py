import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.analysis import Recommendation


class MarketListingResponse(BaseModel):
    """Anúncio individual do ML — link direto para o produto pesquisado."""

    model_config = {"from_attributes": True}

    rank_position: int
    item_id: str
    title: str
    price: Decimal
    sold_quantity: int | None
    permalink: str | None
    thumbnail: str | None
    match_confidence: Decimal | None
    # Frete e taxa real por anúncio
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
    # Taxa ML real média (None = enriquecimento falhou → usou taxa de config)
    avg_ml_fee_pct: Decimal | None = None
    # % de anúncios com frete grátis (0–100). >50 = mercado espera frete grátis.
    free_shipping_pct: Decimal | None = None
    # Top anúncios ML com links diretos (carregados separadamente)
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
    # Margem líquida real (após frete + imposto)
    net_margin: Decimal | None = None
    net_margin_pct: Decimal | None = None
    # Preço mínimo para atingir 20% de margem líquida
    min_price_for_target_margin: Decimal | None = None


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
    recomme