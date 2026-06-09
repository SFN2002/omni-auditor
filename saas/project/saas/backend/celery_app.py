"""
Omni-Auditor SaaS Dashboard — Celery Application Configuration.

Configures the Celery app with Redis broker, proper serialization,
and task routes for the background worker.
"""

from __future__ import annotations

from celery import Celery

from saas.backend.config import settings

# ── Celery App ────────────────────────────────────────────────

celery_app = Celery(
    "omni_auditor",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.REDIS_URL,
    include=[
        "saas.backend.tasks",
    ],
)

# ── Configuration ─────────────────────────────────────────────

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task execution
    task_track_started=True,
    task_time_limit=600,          # 10 minutes hard limit
    task_soft_time_limit=300,     # 5 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,

    # Result backend
    result_expires=3600,          # 1 hour
    result_backend=settings.REDIS_URL,

    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_pool_limit=10,

    # Task routes
    task_routes={
        "saas.backend.tasks.run_omni_auditor_analysis": {"queue": "analysis"},
        "saas.backend.tasks.process_webhook_event": {"queue": "webhooks"},
    },

    # Default queue
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
)

# ── Beat Schedule (for scheduled scans) ───────────────────────

celery_app.conf.beat_schedule = {
    # Add periodic tasks here when needed
    # "daily-scan-scheduler": {
    #     "task": "saas.backend.tasks.schedule_daily_scans",
    #     "schedule": 86400.0,  # every 24 hours
    # },
}
