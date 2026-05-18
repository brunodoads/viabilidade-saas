"""
Configuração do Celery.

Design MVP:
- 1 fila padrão (default)
- 1 worker processo
- Retry automático com backoff exponencial
- Resultado armazenado no Redis (TTL 24h)
"""

import ssl

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "viabilidade",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

# Railway Redis usa rediss:// (TLS). Celery exige configuração SSL explícita
# para não levantar E_REDIS_SSL_CERT_REQS_MISSING_INVALID.
# CERT_NONE é seguro em ambientes gerenciados como Railway onde a cadeia de
# confiança é controlada pela plataforma.
_ssl_params = {"ssl_cert_reqs": ssl.CERT_NONE}

if settings.CELERY_BROKER_URL.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = _ssl_params

if settings.CELERY_RESULT_BACKEND.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = _ssl_params

celery_app.conf.update(
    # ── Serialização ────────────────────────────────────────────────────────
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── Timezone ─────────────────────────────────────────────────────────────
    timezone="America/Sao_Paulo",
    enable_utc=True,

    # ── Resultado ────────────────────────────────────────────────────────────
    result_expires=86400,  # TTL do resultado no Redis: 24h
    task_ignore_result=False,  # Manter resultado para debug

    # ── Retry padrão ─────────────────────────────────────────────────────────
    task_acks_late=True,   # Confirma task só após execução (evita perda no crash)
    task_reject_on_worker_lost=True,

    # ── Worker ───────────────────────────────────────────────────────────────
    worker_prefetch_multiplier=1,  # 1 task por vez (pipeline é sequential IO-bound)
    task_track_started=True,       # Status "STARTED" visível

    # ── Filas ────────────────────────────────────────────────────────────────
    # MVP usa apenas 1 fila. Fase 2 adiciona filas especializadas:
    # task_routes = {
    #     "app.workers.tasks.process_catalog_task": {"queue": "default"},
    #     "app.workers.tasks.research_product_task": {"queue": "research"},
    # }
    task_default_queue="default",
    task_queues={
        "default": {
            "exchange": "default",
            "routing_key": "default",
        }
    },
)
