"""add catalog file_content column

Revision ID: 004
Revises: 003
Create Date: 2026-05-18

Contexto:
  Backend e worker Celery rodam em containers SEPARADOS — sem filesystem compartilhado.
  Solução MVP: guardar o conteúdo binário do arquivo no PostgreSQL (BYTEA).
  Fase 2: migrar para S3/Supabase Storage sem mudar a interface do scout_service.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalogs",
        sa.Column(
            "file_content",
            sa.LargeBinary(),
            nullable=True,
            comment=(
                "Conteúdo binário do arquivo para acesso cross-container. "
                "MVP: BYTEA PostgreSQL. Fase 2: S3/Supabase Storage."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("catalogs", "file_content")
