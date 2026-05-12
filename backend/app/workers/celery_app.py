"""Celery app constructor.

Single broker (Redis) + single result backend. Tasks live in
`app.workers.*` and are auto-discovered by include.

Tests run with `task_always_eager=True` so the task executes inline
on `.delay(...)` — no real broker required.
"""

from __future__ import annotations

import os

from celery import Celery

from app.config import get_settings


def _build_app() -> Celery:
    cfg = get_settings()
    app = Celery(
        "taxmind_books",
        broker=cfg.REDIS_URL,
        backend=cfg.REDIS_URL,
        include=[
            "app.workers.posting_tasks",
            "app.workers.lifecycle_tasks",
        ],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )
    # Tests opt into eager mode via env so .delay() runs the task
    # inline on the calling thread.
    if os.environ.get("CELERY_TASK_ALWAYS_EAGER") == "1":
        app.conf.task_always_eager = True
        app.conf.task_eager_propagates = True
    return app


celery_app: Celery = _build_app()
