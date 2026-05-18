"""
conftest.py — Configuração global de testes.

Define env vars mínimas antes de qualquer import da app,
evitando que Settings() falhe por falta de DATABASE_URL/SECRET_KEY.
"""

import os
import tempfile
from pathlib import Path

# ── Env vars mínimas para testes de unit/integração ──────────────────────────
# Devem ser setadas ANTES de qualquer import de app.*
# Testes de integração que precisem de banco real devem sobrescrever via fixture.

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-only-for-unit-tests-not-for-production")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CLAUDE_API_KEY", "sk-ant-test-key")
os.environ.setdefault("ML_FEE_PCT", "15.0")
os.environ.setdefault("MIN_SALES_THRESHOLD", "1000")

import pytest


# ── tmp_path override ─────────────────────────────────────────────────────────
# O pytest usa tmp_path no filesystem montado (Windows), que causa erros de
# permissão ao limpar. Redirecionamos para /tmp do Linux sandbox.

@pytest.fixture
def tmp_path(tmp_path_factory):
    """
    Cria diretório temporário em /tmp (filesystem nativo do Linux).
    Evita erros de permissão no filesystem montado (CIFS/Windows).
    """
    base = Path("/tmp/pytest_tests")
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as d:
        yield Path(d)
