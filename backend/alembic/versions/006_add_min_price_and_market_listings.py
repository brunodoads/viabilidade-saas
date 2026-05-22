"""add min_price_for_target_margin and market_listings table

Revision ID: 006
Revises: 005
Create Date: 2026-05-22

Contexto:
  Dois objetivos nesta migration:

  1. Adicionar min_price_for_target_margin em financial_analyses.
     Preço mínimo de venda para atingir margem líquida alvo (padrão 20%).
     Fórmula: (cost + shipping) / (1 - ml_fee_pct - tax_pct - target_pct)
     Exemplo: custo=R$78 + frete=R$20, fee=11%, imposto=7%, target=20%
     → min_price = 98 / (1 - 0.11 - 0.07 - 0.20) = 98 / 0.62 = R$158,06

  2. Criar tabela market_listings.
     Armazena os top 5 anúncios ML qualificados por produto.
     Permite mostrar links diretos + thumbnails no frontend.

     Índice único (product_id, rank_position) garante upsert correto:
     ao re-analisar um produto, os listings antigos são substituídos.

  Também alargar net_margin_pct de Numeric(5,2) para Numeric(10,2):
  em produtos muito caros vs ML, net_margin_pct pode ser < -999.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Adicionar min_price_for_target_margin em financial_analyses
    op.add_column(
        "financial_analyses",
        sa.Column(
            "min_price_for_target_margin",
            sa.Numeric(12, 2),
            nullable=True,
            comment="Preço mínimo para atingir margem líquida alvo (padrão 20%)",
        ),
    )

    # 2. Alargar net_margin_pct para evitar overflow em produtos muito caros
    op.alter_column(
        "financial_analyses",
        "net_margin_pct",
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
    )

    # 3. Criar tabela market_listings
    op.create_table(
        "market_listings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank_position", sa.Integer, nullable=False),
        sa.Column("item_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("sold_quantity", sa.Integer, nullable=True),
        sa.Column("permalink", sa.Text, nullable=True),
        sa.Column("thumbnail", sa.Text, nullable=True),
        sa.Column("match_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Índice para queries por produto (ORDER BY rank_position)
    op.create_index(
        "ix_market_listings_product_id",
        "market_listings",
        ["product_id"],
    )

    # Índice único: garante que (product_id, rank_position) é único.
    # Permite upsert baseado em rank sem duplicatas.
    op.create_index(
        "uq_market_listings_product_rank",
        "market_listings",
        ["product_id", "rank_position"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_market_listings_product_rank", table_name="market_listings")
    op.drop_index("ix_market_listings_product_id", table_name="market_listings")
    op.drop_table("market_listings")

    op.alter_column(
        "financial_analyses",
        "net_margin_pct",
        type_=sa.Numeric(5, 2),
        existing_nullable=True,
    )

    op.drop_column("financial_analyses", "min_price_for_target_margin")
