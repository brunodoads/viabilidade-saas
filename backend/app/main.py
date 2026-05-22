"""
FastAPI Application — Ponto de entrada principal.

Configuração:
- CORS configurado via settings
- Routers registrados com prefixo /api
- Lifespan verifica conexão com banco no startup
- Logging configurado
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, catalogs, diagnostics, opportunities
from app.core.config import settings
from app.db.session import check_db_connection

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Executado no startup e shutdown da aplicação."""
    # Startup
    logger.info("🚀 Iniciando %s v%s", settings.APP_NAME, settings.APP_VERSION)

    if check_db_connection():
        logger.info("✅ Conexão com banco de dados OK")
    else:
        logger.critical("❌ Falha na conexão com banco de dados — verifique DATABASE_URL")
        # Não impede startup para permitir health check retornar erro

    # Auto-migrate: roda alembic upgrade head no startup.
    # Garante que migrations novas sejam aplicadas automaticamente no Railway
    # sem precisar de um release command separado.
    try:
        from alembic.config import Config
        from alembic import command as alembic_command
        import os

        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("✅ Migrations aplicadas com sucesso (alembic upgrade head)")
    except Exception as exc:
        logger.warning("⚠️  Falha ao aplicar migrations: %s — continuando startup", exc)

    yield

    # Shutdown
    logger.info("⏹️  Encerrando aplicação")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "SaaS de inteligência comercial para análise de viabilidade de produtos no Mercado Livre. "
        "Analisa catálogos de fornecedores e identifica oportunidades lucrativas automaticamente."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# IMPORTANTE: allow_origins=["*"] + allow_credentials=True é inválido pelo spec HTTP
# e faz o browser rejeitar a resposta (header omitido silenciosamente pelo Starlette).
# Usamos origens explícitas via CORS_ORIGINS env var.
# Como usamos Bearer tokens (não cookies), allow_credentials=False é correto.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(catalogs.router, prefix="/api")
app.include_router(opportunities.router, prefix="/api")
app.include_router(diagnostics.router, prefix="/api")


# ── Health Check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Sistema"], summary="Verificar saúde da aplicação")
def health_check() -> dict:
    """
    Endpoint de health check para Railway e load balancers.
    Não requer autenticação.
    """
    db_ok = check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "connected" if db_ok else "disconnected",
    }


@app.get("/", tags=["Sistema"], include_in_schema=False)
def root() -> dict:
    return {"message": f"{settings.APP_NAME} API", "docs": "/docs"}
