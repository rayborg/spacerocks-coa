from __future__ import annotations

import secrets

from fastapi import FastAPI

from app.config.settings import Settings
from app.db.session import create_database_engine, create_session_factory
from app.tasks.composition import create_task_dispatch
from app.tasks.http import create_task_app
from app.worker.composition import build_worker


def create_configured_task_app() -> FastAPI:
    settings = Settings()
    if settings.database_url is None or settings.cloud_tasks_queue is None:
        raise RuntimeError("task_worker_configuration_required")
    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    task_dispatch = create_task_dispatch(settings, session_factory)
    if task_dispatch is None:
        raise RuntimeError("task_dispatch_configuration_required")
    worker = build_worker(
        settings,
        session_factory,
        worker_id=f"task-{secrets.token_hex(8)}",
        task_dispatch=task_dispatch,
    )
    return create_task_app(
        worker,
        task_dispatch,
        expected_queue=settings.cloud_tasks_queue,
    )


app = create_configured_task_app()
