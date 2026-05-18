#!/usr/bin/env python3
"""
validate_env.py — Validação completa do ambiente antes de subir o sistema.

Verifica:
  1. Variáveis de ambiente obrigatórias
  2. Conexão com PostgreSQL
  3. Conexão com Redis
  4. Imports críticos do backend
  5. Versão do Python
  6. Chaves API configuradas (warnings, não erros)

Uso:
  python scripts/validate_env.py              # valida ambiente local
  python scripts/validate_env.py --docker     # valida dentro do container
"""
import os
import sys
import importlib
from pathlib import Path

# ── Configuração ──────────────────────────────────────────────────────────────
RED    = "\033[0;31m"
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE   = "\033[0;34m"
NC     = "\033[0m"

errors   = []
warnings = []
passed   = []


def ok(msg):    passed.append(msg);   print(f"  {GREEN}✓{NC}  {msg}")
def warn(msg):  warnings.append(msg); print(f"  {YELLOW}⚠{NC}  {msg}")
def fail(msg):  errors.append(msg);   print(f"  {RED}✗{NC}  {msg}")
def section(s): print(f"\n{BLUE}── {s} {'─' * (50 - len(s))}{NC}")


# ── 1. Python version ─────────────────────────────────────────────────────────
section("Python")
major, minor = sys.version_info[:2]
if major == 3 and minor >= 11:
    ok(f"Python {major}.{minor} (>= 3.11 requerido)")
else:
    fail(f"Python {major}.{minor} — requer >= 3.11")


# ── 2. Variáveis de ambiente ──────────────────────────────────────────────────
section("Variáveis de Ambiente")

# Tentar carregar .env automaticamente
env_path = Path(__file__).parent.parent / "backend" / ".env"
if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        ok(f".env carregado de {env_path}")
    except ImportError:
        warn("python-dotenv não instalado — carregue o .env manualmente")
else:
    warn(f".env não encontrado em {env_path} — usando variáveis do ambiente")

# Obrigatórias
REQUIRED = {
    "DATABASE_URL": "URL de conexão com PostgreSQL",
    "SECRET_KEY":   "Chave secreta para JWT",
}
for var, desc in REQUIRED.items():
    val = os.getenv(var, "")
    if not val:
        fail(f"{var} não configurada — {desc}")
    elif var == "SECRET_KEY" and "troque" in val.lower():
        warn(f"{var} ainda com valor padrão — troque por uma chave segura!")
    else:
        ok(f"{var} configurada")

# Recomendadas (warnings se ausentes)
RECOMMENDED = {
    "ML_APP_ID":       "App ID do Mercado Livre (busca autenticada)",
    "ML_CLIENT_SECRET": "Client Secret do Mercado Livre",
    "CLAUDE_API_KEY":  "Chave da Claude API (agentes IA)",
}
for var, desc in RECOMMENDED.items():
    val = os.getenv(var, "")
    if not val:
        warn(f"{var} não configurada — {desc}")
    else:
        ok(f"{var} configurada")


# ── 3. Conexão com PostgreSQL ─────────────────────────────────────────────────
section("PostgreSQL")
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    try:
        import sqlalchemy as sa
        engine = sa.create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version()")).scalar()
            ok(f"Conectado — {version[:40]}...")
    except Exception as e:
        fail(f"Falha na conexão: {e}")
else:
    fail("DATABASE_URL não configurada — não foi possível testar conexão")


# ── 4. Conexão com Redis ──────────────────────────────────────────────────────
section("Redis")
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
try:
    import redis
    r = redis.from_url(redis_url, socket_connect_timeout=3)
    r.ping()
    info = r.info("server")
    ok(f"Conectado — Redis {info.get('redis_version', '?')} em {redis_url}")
except Exception as e:
    fail(f"Falha na conexão Redis: {e}")


# ── 5. Imports críticos do backend ────────────────────────────────────────────
section("Imports do Backend")
BACKEND_PATH = Path(__file__).parent.parent / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

CRITICAL_IMPORTS = [
    ("app.core.config",            "settings"),
    ("app.models.analysis",        "MarketAnalysis, FinancialAnalysis, OpportunityScore, Recommendation"),
    ("app.models.catalog",         "Catalog, CatalogStatus"),
    ("app.models.product",         "Product"),
    ("app.services.finance_service", "FeeConfig, calculate"),
    ("app.services.strategy_service","score_product, filter_opportunities"),
    ("app.services.ml_matching",   "match_listings"),
]

for module, symbols in CRITICAL_IMPORTS:
    try:
        importlib.import_module(module)
        ok(f"{module} ({symbols})")
    except ImportError as e:
        fail(f"{module} — ImportError: {e}")
    except Exception as e:
        warn(f"{module} — {type(e).__name__}: {e}")


# ── 6. Estado das migrations ──────────────────────────────────────────────────
section("Migrations Alembic")
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        import sqlalchemy as sa

        alembic_cfg_path = BACKEND_PATH / "alembic.ini"
        engine = sa.create_engine(db_url)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()

        if current:
            ok(f"Revision atual: {current}")
            if current != "003":
                warn(f"Revision desatualizada — esperado 003, encontrado {current}. Rode: alembic upgrade head")
            else:
                ok("Banco em dia com o código")
        else:
            warn("Banco não inicializado — rode: alembic upgrade head")
    except Exception as e:
        warn(f"Não foi possível verificar migrations: {e}")
else:
    warn("DATABASE_URL ausente — migrations não verificadas")


# ── Resumo ────────────────────────────────────────────────────────────────────
print(f"\n{'═' * 55}")
print(f"  {GREEN}Passou:   {len(passed)}{NC}   "
      f"{YELLOW}Avisos: {len(warnings)}{NC}   "
      f"{RED}Erros:  {len(errors)}{NC}")
print(f"{'═' * 55}")

if errors:
    print(f"\n{RED}Sistema NÃO está pronto. Corrija os erros acima.{NC}")
    sys.exit(1)
elif warnings:
    print(f"\n{YELLOW}Sistema pode subir, mas verifique os avisos.{NC}")
    sys.exit(0)
else:
    print(f"\n{GREEN}Tudo certo! Sistema pronto para rodar.{NC}")
    sys.exit(0)
