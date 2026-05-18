"""Initial schema — users, catalogs, products, analyses

Revision ID: 001
Revises:
Create Date: 2026-05-14 00:00:00.000000

Cria todas as tabelas do MVP inicial:
- users
- catalogs
- products
- market_analyses
- financial_analyses
- opportunity_scores

Inclui enums PostgreSQL:
- filetype_enum (PDF, XLSX, CSV)
- catalogstatus_enum (PENDING, PARSING, RESEARCHING, ANALYZING, SCORING, READY, ERROR)
- recommendation_enum (ALTA, MEDIA, BAIXA, EVITAR)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums PostgreSQL ─────────────────────────────────────────────────────
    filetype_enum = postgresql.ENUM(
        "PDF", "XLSX", "CSV",
        name="filetype_enum",
        create_type=False,
    )
    catalogstatus_enum = postgresql.ENUM(
        "PENDING", "PARSING", "RESEARCHING", "ANALYZING", "SCORING", "READY", "ERROR",
        name="catalogstatus_enum",
        create_type=False,
    )
    recommendation_enum = postgresql.ENUM(
        "ALTA", "MEDIA", "BAIXA", "EVITAR",
        name="recommendation_enum",
        create_type=False,
    )


    # ── Tabela: users ────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── Tabela: catalogs ─────────────────────────────────────────────────────
    op.create_table(
        "catalogs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column(
            "file_type",
            sa.Enum("PDF", "XLSX", "CSV", name="filetype_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "PARSING", "RESEARCHING", "ANALYZING",
                "SCORING", "READY", "ERROR",
                name="catalogstatus_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_products", sa.Integer(), nullable=True),
        sa.Column("processed_products", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalogs_user_id", "catalogs", ["user_id"])
    op.create_index("ix_catalogs_status", "catalogs", ["status"])

    # ── Tabela: products ─────────────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=True),
        sa.Column("sku", sa.String(100), nullable=True),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("supplier", sa.String(255), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalogs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_catalog_id", "products", ["catalog_id"])
    op.create_index("ix_products_user_id", "products", ["user_id"])

    # ── Tabela: market_analyses ──────────────────────────────────────────────
    op.create_table(
        "market_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("min_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_sellers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_listings_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listings_above_threshold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_market_analyses_product_id"),
    )
    op.create_index("ix_market_analyses_product_id", "market_analyses", ["product_id"])

    # ── Tabela: financial_analyses ───────────────────────────────────────────
    op.create_table(
        "financial_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),

        # Inputs
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("avg_market_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("marketplace_fee_pct", sa.Numeric(5, 2), nullable=False, server_default="15.00"),

        # Outputs MVP
        sa.Column("gross_revenue", sa.Numeric(12, 2), nullable=False),
        sa.Column("gross_margin", sa.Numeric(12, 2), nullable=False),
        sa.Column("gross_margin_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("break_even_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_viable", sa.Boolean(), nullable=False, server_default="false"),

        # Fase 2 — criadas como NULL para evitar migration futura
        sa.Column("ads_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("return_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("packaging_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_margin", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_margin_pct", sa.Numeric(5, 2), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_financial_analyses_product_id"),
    )
    op.create_index("ix_financial_analyses_product_id", "financial_analyses", ["product_id"])

    # ── Tabela: opportunity_scores ───────────────────────────────────────────
    op.create_table(
        "opportunity_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("demand_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("margin_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("competition_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("final_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "recommendation",
            sa.Enum("ALTA", "MEDIA", "BAIXA", "EVITAR", name="recommendation_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_opportunity_scores_product_id"),
    )
    op.create_index("ix_opportunity_scores_product_id", "opportunity_scores", ["product_id"])
    op.create_index("ix_opportunity_scores_final_score", "opportunity_scores", ["final_score"])


def downgrade() -> None:
    # Drop tables em ordem reversa de dependência
    op.drop_table("opportunity_scores")
    op.drop_table("financial_analyses")
    op.drop_table("market_analyses")
    op.drop_table("products")
    op.drop_table("catalogs")
    op.drop_table("users")

    # Drop enums PostgreSQL
    op.execute("DROP TYPE IF EXISTS recommendation_enum")
    op.execute("DROP TYPE IF EXISTS catalogstatus_enum")
    op.execute("DROP TYPE IF EXISTS filetype_enum")
