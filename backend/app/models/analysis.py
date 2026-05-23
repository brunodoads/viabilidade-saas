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
    """
    Dados de mercado coletados do Mercado Livre para um produto.

    Armazena o resumo consolidado dos anúncios qualificados (>= MIN_SALES_THRESHOLD vendas
    e matching confidence >= ML_MIN_MATCH_CONFIDENCE).

    Cada produto tem no máximo 1 análise de mercado (relacionamento 1:1).
    """

    __tablename__ = "market_analyses"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ── Dados de preço (anúncios filtrados) ──────────────────────────────────
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # ── Dados de demanda ──────────────────────────────────────────────────────
    avg_sold_quantity: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Média de unidades vendidas nos anúncios qualificados — sinal primário de demanda",
    )
    total_sold_quantity: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Total de unidades vendidas (soma de todos os anúncios qualificados)",
    )

    # ── Dados de competição ───────────────────────────────────────────────────
    total_sellers: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Nº de vendedores únicos nos anúncios filtrados",
    )
    total_listings_found: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total de anúncios brutos retornados pela API antes dos filtros",
    )
    listings_above_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Anúncios aprovados: vendas >= MIN_SALES_THRESHOLD e matching >= MIN_CONFIDENCE",
    )

    # ── Qualidade do matching ─────────────────────────────────────────────────
    avg_match_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
        comment="Confiança média do matching nos anúncios aprovados (0.000–1.000)",
    )

    # ── Taxa real ML e cobertura de frete grátis ──────────────────────────────
    # Calculados durante o enriquecimento — usados pelo Finance Agent.
    # avg_ml_fee_pct: média das taxas reais por categoria dos anúncios qualificados.
    #   None = todos os anúncios falharam no enriquecimento → usa taxa padrão (config).
    # free_shipping_pct: % de anúncios com frete grátis.
    #   Se >50%: mercado espera frete grátis → custo de envio deve ser orçado.
    avg_ml_fee_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Taxa média real do ML em % (via Listing Prices API) — substitui taxa fixa de config",
    )
    free_shipping_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="% de anúncios qualificados com frete grátis (0–100) — sinal de expectativa do mercado",
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Momento da coleta — para cache futuro (TTL 1h)",
    )

    # Relacionamento
    product: Mapped[Product] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="market_analysis",
    )

    def __repr__(self) -> str:
        return (
            f"<MarketAnalysis product_id={self.product_id} "
            f"avg_price={self.avg_price} sellers={self.total_sellers}>"
        )


class FinancialAnalysis(Base, UUIDPrimaryKeyMixin):
    """
    Análise financeira calculada para um produto.

    Fórmula MVP:
        ml_fee          = avg_market_price * (marketplace_fee_pct / 100)
        gross_margin    = avg_market_price - cost - ml_fee
        gross_margin_pct = (gross_margin / avg_market_price) * 100
        break_even_price = cost / (1 - marketplace_fee_pct / 100)
        break_even_qty   = custo_fixo / gross_margin  [reservado para Fase 2]
        is_viable        = gross_margin > 0

    Colunas para custos futuros (Fase 2) são criadas como NULL desde o início
    para evitar ALTER TABLE quando a feature for implementada.
    """

    __tablename__ = "financial_analyses"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ── Inputs do cálculo ────────────────────────────────────────────────────
    cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Custo do produto na época do cálculo (snapshot)",
    )
    avg_market_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Preço médio ML usado no cálculo",
    )
    marketplace_fee_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("15.00"),
        comment="Taxa do marketplace em % (padrão 15% ML)",
    )

    # ── Resultados MVP ───────────────────────────────────────────────────────
    gross_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Receita bruta = avg_market_price (referência para cálculo de taxa)",
    )
    ml_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        comment="Valor absoluto da taxa ML em R$ (gross_revenue * marketplace_fee_pct / 100)",
    )
    gross_margin: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Margem bruta em R$ = receita - custo - taxa ML",
    )
    gross_margin_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),  # Widened from (5,2): can be <-1000% when cost >> market price
        nullable=False,
        comment="Margem bruta em % sobre a receita",
    )
    break_even_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Preço mínimo para cobrir custo + taxa ML sem lucro",
    )
    price_safety_margin_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),  # Widened from (5,2): can be >1000% when cost << market price
        nullable=True,
        comment="Quanto o preço médio está acima do break_even em % — margem de segurança",
    )
    is_viable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True se gross_margin > 0 (produto cobre custos com lucro)",
    )

    # ── Fase 2: Custos adicionais ─────────────────────────────────────────────
    # Slots criados como NULL para evitar ALTER TABLE quando implementados.
    # O Finance Agent simplesmente os preenche quando disponíveis.
    ads_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="[Fase 2] Custo estimado de ADS por unidade vendida",
    )
    return_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="[Fase 2] Taxa de devolução (0.0000–1.0000)",
    )
    packaging_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="[Fase 2] Custo de embalagem por unidade",
    )
    fulfillment_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="[Fase 2] Custo de fulfillment por unidade",
    )
    tax_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="[Fase 2] Custo de impostos por unidade (além do simples incluso no fee)",
    )
    net_margin: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Margem líquida em R$ (deduzidos frete + imposto + ADS + devoluções)",
    )
    net_margin_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="Margem líquida em % sobre receita",
    )
    min_price_for_target_margin: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Preço mínimo de venda para atingir a margem líquida alvo (padrão 20%)",
    )
    # ─────────────────────────────────────────────────────────────────────────

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relacionamento
    product: Mapped[Product] = relationship(  # type: ignore[name-defined]
        "Product",
        back_populates="financial_analysis",
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialAnalysis product_id={self.product_id} "
            f"margin={self.gross_margin_pct}% viable={self.is_viable}>"
        )


class Recommendation(str, enum.Enum):
    """
    Classificação estratégica final de uma oportunidade.

    Baseada no score final ponderado (0-100):
        EXCELENTE  >= 75  — forte oportunidade, agir com prioridade
        BOA        >= 55  — oportunidade sólida, analisar mais
        ARRISCADA  >= 35  — margem ou demanda limitada, cautela
        EVITAR     <  35  — não recomendado no momento
    """

    EXCELENTE = "EXCELENTE"
    BOA = "BOA"
    ARRISCADA = "ARRISCADA"
    EVITAR = "EVITAR"


class OpportunityScore(Base, UUIDPrimaryKeyMixin):
    """
    Score estratégico final calculado para uma oportunidade.

    Fórmula MVP (pesos fixos, soma = 1.0):
        demand_score       = sinal de demanda (0–100) — peso 35%
        margin_score       = sinal de margem  (0–100) — peso 40%
        competition_score  = sinal de concorrência (0–100) — peso 15%
        confidence_score   = sinal de qualidade do matching (0–100) — peso 10%

        final_score = (demand * 0.35) + (margin * 0.40) + (competition * 0.15) + (confidence * 0.10)

    Fase 2: pesos configuráveis por organização em organizations.score_weights.
    """

    __tablename__ = "opportunity_scores"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ── Scores componentes (0–100) ────────────────────────────────────────────
    demand_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Score de demanda baseado em volume médio de vendas (0–100)",
    )
    margin_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Score de margem baseado em gross_margin_pct (0–100)",
    )
    competition_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Score de concorrência — menos vendedores = score maior (0–100)",
    )
    confidence_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("100.00"),
        comment="Score de confiança do matching ML (0–100) — qualidade dos dados",
    )

    # ── Score final e classificação ───────────────────────────────────────────
    final_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        index=True,
        comment="Score final ponderado (0–100)",
    )
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Posição no rank