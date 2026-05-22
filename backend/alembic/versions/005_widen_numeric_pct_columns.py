"""widen Numeric(5,2) pct columns to Numeric(10,2)

Revision ID: 005
Revises: 004
Create Date: 2026-05-22

Contexto:
  Campos de percentual declarados como Numeric(5,2) têm range -999.99 a 999.99.
  Produtos onde o preço ML é muito maior que o custo do catálogo geram
  price_safety_margin_pct > 1000% (ex: custo=8.50, ML=190 → 1807%).
  Isso causa DataError no PostgreSQL + corrupção da sessão SQLAlchemy.

  Campos afetados em financial_analyses:
    - price_safety_margin_pct: pode ser >1000% (produto muito barato vs ML)
    - gross_margin_pct: pode ser <-1000% (produto muito caro vs ML)

  Solução: ampliar para Numeric(10,2) → range -99999999.99 a 99999999.99.
  O cap em código (max 999.99) continua como camada extra de proteção.

  Campos NÃO alterados (valores sempre em 0-100 por design):
    - marketplace_fee_pct: sempre 0-100% → Numeric(5,2) OK
    - demand_score, margin_score etc. em opportunity_scores: 0-100 → OK
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alargar price_safety_margin_pct: pode exceder 999.99% em produtos baratos
    op.alter_column(
        "financial_analyses",
        "price_safety_margin_pct",
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
    )

    # Alargar gross_margin_pct: pode ser muito negativo em produtos caros vs ML
    op.alter_column(
        "financial_analyses",
        "gross_margin_pct",
        type_=sa.Numeric(10, 2),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Volta para Numeric(5,2) — ATENÇÃO: dados fora de range serão truncados
    op.alter_column(
        "financial_analyses",
        "gross_margin_pct",
        type_=sa.Numeric(5, 2),
        existing_nullable=False,
    )
    op.alter_column(
        "financial_analyses",
        "price_safety_margin_pct",
        type_=sa.Numeric(5, 2),
        existing_nullable=True,
    )
