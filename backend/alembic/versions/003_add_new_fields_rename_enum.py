"""Adicionar novos campos e renomear enum Recommendation

Revision ID: 003
Revises: 002
Create Date: 2026-05-15 00:00:00.000000

Adiciona campos introduzidos nas Tasks #20-22:

market_analyses:
  + avg_sold_quantity      INTEGER NULL  — média de unidades vendidas (demanda primária)
  + total_sold_quantity    INTEGER NULL  — soma total de vendas nos anúncios
  + avg_match_confidence   NUMERIC(4,3) NULL — confiança média do matching ML

financial_analyses:
  + ml_fee                 NUMERIC(12,2) — valor absoluto da taxa ML em R$
  + price_safety_margin_pct NUMERIC(5,2) NULL — % acima do break_even
  + fulfillment_cost       NUMERIC(12,2) NULL — [Fase 2] custo fulfillment
  + tax_cost               NUMERIC(12,2) NULL — [Fase 2] custo de impostos

opportunity_scores:
  + confidence_score       NUMERIC(5,2) — score de qualidade do matching (0-100)
  + explanation            TEXT NULL — explicação textual em português

recommendation_enum rename:
  ALTA  → EXCELENTE
  MEDIA → BOA
  BAIXA → ARRISCADA
  EVITAR permanece

DOWNGRADE SEGURO:
  Remove as colunas adicionadas.
  Reverte o rename do enum.
  Linhas com EXCELENTE/BOA/ARRISCADA são convertidas de volta antes do rename.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────────────
    # 1. Renomear valores do enum recommendation_enum
    #    ALTA → EXCELENTE | MEDIA → BOA | BAIXA → ARRISCADA
    #    Usa ALTER TYPE ... RENAME VALUE (PostgreSQL >= 10)
    #    É idempotente: se o valor já existe com o novo nome, falha silenciosamente
    # ─────────────────────────────────────────────────────────────────────────
    conn = op.get_bind()

    # Verificar se precisa renomear (evita falha em re-run)
    result = conn.execute(
        sa.text(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'recommendation_enum' "
            "ORDER BY enumsortorder"
        )
    ).fetchall()
    current_values = {row[0] for row in result}

    if "ALTA" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'ALTA' TO 'EXCELENTE'")
    if "MEDIA" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'MEDIA' TO 'BOA'")
    if "BAIXA" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'BAIXA' TO 'ARRISCADA'")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. market_analyses — novos campos de demanda e qualidade
    # ─────────────────────────────────────────────────────────────────────────
    op.add_column(
        "market_analyses",
        sa.Column(
            "avg_sold_quantity",
            sa.Integer(),
            nullable=True,
            comment="Média de unidades vendidas nos anúncios qualificados",
        ),
    )
    op.add_column(
        "market_analyses",
        sa.Column(
            "total_sold_quantity",
            sa.Integer(),
            nullable=True,
            comment="Total de unidades vendidas (soma dos anúncios qualificados)",
        ),
    )
    op.add_column(
        "market_analyses",
        sa.Column(
            "avg_match_confidence",
            sa.Numeric(4, 3),
            nullable=True,
            comment="Confiança média do matching ML (0.000–1.000)",
        ),
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 3. financial_analyses — ml_fee + price_safety + fase 2 slots
    # ─────────────────────────────────────────────────────────────────────────
    op.add_column(
        "financial_analyses",
        sa.Column(
            "ml_fee",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0.00",
            comment="Valor absoluto da taxa ML em R$",
        ),
    )
    op.add_column(
        "financial_analyses",
        sa.Column(
            "price_safety_margin_pct",
            sa.Numeric(5, 2),
            nullable=True,
            comment="% acima do break_even — margem de segurança do preço",
        ),
    )
    op.add_column(
        "financial_analyses",
        sa.Column(
            "fulfillment_cost",
            sa.Numeric(12, 2),
            nullable=True,
            comment="[Fase 2] Custo de fulfillment por unidade",
        ),
    )
    op.add_column(
        "financial_analyses",
        sa.Column(
            "tax_cost",
            sa.Numeric(12, 2),
            nullable=True,
            comment="[Fase 2] Custo de impostos por unidade",
        ),
    )

    # Preencher ml_fee para linhas existentes (retroativo)
    # ml_fee = avg_market_price * marketplace_fee_pct / 100
    op.execute(
        sa.text(
            "UPDATE financial_analyses "
            "SET ml_fee = ROUND(avg_market_price * marketplace_fee_pct / 100, 2) "
            "WHERE ml_fee = 0.00"
        )
    )

    # Remover server_default após preenchimento (coluna deve ser calculada, não defaultada)
    op.alter_column("financial_analyses", "ml_fee", server_default=None)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. opportunity_scores — confidence_score + explanation
    # ─────────────────────────────────────────────────────────────────────────
    op.add_column(
        "opportunity_scores",
        sa.Column(
            "confidence_score",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="100.00",
            comment="Score de confiança do matching ML (0–100)",
        ),
    )
    op.add_column(
        "opportunity_scores",
        sa.Column(
            "explanation",
            sa.Text(),
            nullable=True,
            comment="Explicação textual gerada pelo Strategy Agent",
        ),
    )

    # Remover server_default — será preenchido pelo pipeline
    op.alter_column("opportunity_scores", "confidence_score", server_default=None)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Índices de busca adicionais
    # ─────────────────────────────────────────────────────────────────────────
    op.create_index(
        "ix_opportunity_scores_recommendation",
        "opportunity_scores",
        ["recommendation"],
    )


def downgrade() -> None:
    # ─────────────────────────────────────────────────────────────────────────
    # Reverter em ordem inversa à do upgrade
    # ─────────────────────────────────────────────────────────────────────────

    # 5. Remover índices
    op.drop_index("ix_opportunity_scores_recommendation", table_name="opportunity_scores")

    # 4. Remover colunas opportunity_scores
    op.drop_column("opportunity_scores", "explanation")
    op.drop_column("opportunity_scores", "confidence_score")

    # 3. Remover colunas financial_analyses
    op.drop_column("financial_analyses", "tax_cost")
    op.drop_column("financial_analyses", "fulfillment_cost")
    op.drop_column("financial_analyses", "price_safety_margin_pct")
    op.drop_column("financial_analyses", "ml_fee")

    # 2. Remover colunas market_analyses
    op.drop_column("market_analyses", "avg_match_confidence")
    op.drop_column("market_analyses", "total_sold_quantity")
    op.drop_column("market_analyses", "avg_sold_quantity")

    # 1. Reverter rename do enum
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'recommendation_enum'"
        )
    ).fetchall()
    current_values = {row[0] for row in result}

    if "EXCELENTE" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'EXCELENTE' TO 'ALTA'")
    if "BOA" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'BOA' TO 'MEDIA'")
    if "ARRISCADA" in current_values:
        op.execute("ALTER TYPE recommendation_enum RENAME VALUE 'ARRISCADA' TO 'BAIXA'")
