"""
Alembic Environment Configuration.

Este arquivo configura como o Alembic:
1. Conecta ao banco de dados
2. Descobre os models para autogenerate
3. Executa migrations online (conectado) e offline (gerando SQL)
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Adiciona o diretório raiz ao PATH para importar app ──────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Importar todos os models para autogenerate detectá-los ───────────────────
from app.models import Base  # noqa: E402
from app.core.config import settings  # noqa: E402

# ── Configuração do Alembic ───────────────────────────────────────────────────
config = context.config

# Injetar DATABASE_URL do .env em vez de alembic.ini
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Configurar logging conforme alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData alvo — contém todos os models importados acima
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Executa migrations em modo 'offline'.
    Gera SQL sem conexão com banco — útil para revisar antes de aplicar.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,     # Detecta mudanças de tipo de coluna
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Executa migrations em modo 'online'.
    Conecta ao banco e aplica as migrations diretamente.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # Pool sem conexões persistentes para migrations
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
