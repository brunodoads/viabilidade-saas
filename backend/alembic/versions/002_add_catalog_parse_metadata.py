"""Add parse_metadata JSONB to catalogs

Revision ID: 002
Revises: 001
Create Date: 2026-05-14 00:01:00.000000

Adiciona coluna parse_metadata (JSONB) à tabela catalogs.
Armazena resultado do parsing: confiança, estatísticas e warnings.

Estrutura armazenada:
{
    "confidence": "RELIABLE" | "PARTIAL" | "FAILED",
    "stats": {
        "total_rows_scanned": int,
        "valid_products": int,
        "success_rate": float,
        "skipped": {...},
        ...
    },
    "column_mapping": {...},
    "warnings": [...],
    "errors": [...]
}
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "catalogs",
        sa.Column(
            "parse_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Resultado do parsing: confiança, estatísticas, warnings (ParseResult)",
        ),
    )


def downgrade() -> None:
    op.drop_column("catalogs", "parse_metadata")
