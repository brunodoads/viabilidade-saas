"""
Configuração do Celery.

Design MVP:
- 1 fila padrão (default)
- 1 worker processo
- Retry automático com backoff exponencial
- Resultado armazenado no Redis (TTL 24h)
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "viabilidade",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

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

    # ── Rate limiting global ──────────────────────────────────────────────────
    # O throttle real é feito dentro do market_service (time.sleep)
    # mas podemos limitar tasks por segundo como segunda camada
    # task_default_rate_limit="5/m",  # Descomentar se necessário

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
