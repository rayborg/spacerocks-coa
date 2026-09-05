from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings, TaskDispatchMode
from app.tasks.dispatch import CloudTasksCreator, TaskDispatchCoordinator


def create_task_dispatch(
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> TaskDispatchCoordinator | None:
    if settings.task_dispatch_mode == TaskDispatchMode.DISABLED:
        return None
    values = (
        settings.cloud_tasks_project,
        settings.cloud_tasks_location,
        settings.cloud_tasks_queue,
        settings.cloud_tasks_worker_url,
        settings.cloud_tasks_service_account_email,
        settings.cloud_tasks_audience,
    )
    if any(value is None for value in values):
        raise RuntimeError("validated_cloud_tasks_configuration_unavailable")
    project, location, queue, worker_url, service_account_email, audience = values
    assert project is not None
    assert location is not None
    assert queue is not None
    assert worker_url is not None
    assert service_account_email is not None
    assert audience is not None
    creator = CloudTasksCreator(
        project=project,
        location=location,
        queue=queue,
        worker_url=worker_url,
        service_account_email=service_account_email,
        audience=audience,
    )
    return TaskDispatchCoordinator(session_factory, creator)
