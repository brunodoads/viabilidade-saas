"""add shipping and real ML fee fields

Revision ID: 007
Revises: 006
Create Date: 2026-05-23

Contexto:
  Implementação de taxa ML real por categoria e dados de frete por anúncio.

  Problema anterior:
    - Taxa ML fixa (11%) era usada para TODOS os produtos.
    - Na prática, varia de ~6% (livros) a ~17% (eletrônicos de nicho).
    - Frete fixo de R$20 independente de cobertura real do mercado.

  Solução:
    1. market_listings: novos campos por anúncio individual
       - free_shipping: bool — se o vendedor oferece frete grátis
       - logistic_type: varchar — fulfillment, drop_off, self_service, not_specified
       - ml_fee_pct: numeric — taxa real desta categoria/preço (Listing Prices API)
       - category_id: varchar — ID da categoria ML (necessário para calcular taxa)

    2. market_analyses: novos campos agregados (calculados sobre os top listings)
       - avg_ml_fee_pct: taxa média real dos anúncios qualificados
         → Finance Agent usa em vez da taxa fixa de config
       - free_shipping_pct: % de anúncios com frete grátis
         → Se >50%: mercado espera frete grátis → seller deve absorver custo R$20
         → Se <=50%: frete cobrado do comprador → custo interno R$0

  Impacto no cálculo financeiro:
    Antes: taxa=11% + frete=R$20 para todos
    Depois: taxa=taxa_real_categoria + frete=R$20 se mercado exige, senão R$0
    Mais preciso → scores mais confiáveis → menos falsos positivos/negativos.

  Dados existentes:
    - avg_ml_fee_pct e free_shipping_pct ficam NULL para análises antigas.
      Finance Agent detecta NULL e usa taxa/frete de config como fallback.
    - Novo processamento preencherá automaticamente.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. market_listings: campos por anúncio ────────────────────────────────
    op.add_column(
        "market_listings",
        sa.Column(
            "free_shipping",
            sa.Boolean,
            nullable=True,
            comment="True se o vendedor oferece frete grátis neste anúncio",
        ),
    )
    op.add_column(
        "market_listings",
        sa.Column(
            "logistic_type",
            sa.String(50),
            nullable=True,
            comment="Tipo de logística: fulfillment, drop_off, self_service, not_specified",
        ),
    )
    op.add_column(
        "market_listings",
        sa.Column(
            "ml_fee_pct",
            sa.Numeric(5, 2),
            nullable=True,
            comment="Taxa real do ML em % para esta categoria/preço (Listing Prices API)",
        ),
    )
    op.add_column(
        "market_listings",
        sa.Column(
            "category_id",
            sa.String(20),
            nullable=True,
            comment="ID da categoria ML (ex: MLB1648) — usado para calcular taxa real",
        ),
    )

    # ── 2. market_analyses: campos agregados ─────────────────────────────────
    op.add_column(
        "market_analyses",
        sa.Column(
            "avg_ml_fee_pct",
            sa.Numeric(5, 2),
            nullable=True,
            comment="Taxa média real do ML em % — substitui taxa fixa de config quando disponível",
        ),
    )
    op.add_column(
        "market_analyses",
        sa.Column(
            "free_shipping_pct",
            sa.Numeric(5, 2),
            nullable=True,
            comment="% de anúncios qualificados com frete grátis (0–100)",
        ),
    )


def downgrade() -> None:
    op.drop_column("market_analyses", "free_shipping_pct")
    op.drop_column("market_analyses", "avg_ml_fee_pct")

    op.drop_column("market_listings", "category_id")
    op.drop_column("market_listings", "ml_fee_pct")
    op.drop_column("market_listings", "logistic_type")
    op.drop_column("market_listings", "free_shipping")
