from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DurableJob, JobAttempt, TaskDispatch

_TASK_NAME = re.compile(r"^timestamp-[0-9a-f]{32}-g[1-9][0-9]{0,8}$")
MIN_STALE_DISPATCH_GRACE = timedelta(hours=2)
MAX_STALE_DISPATCH_GRACE = timedelta(days=30)


class TaskDispatchUnavailable(RuntimeError):
    pass


def deterministic_task_name(job_id: uuid.UUID, generation: int) -> str:
    if generation < 1:
        raise ValueError("task_generation_invalid")
    name = f"timestamp-{job_id.hex}-g{generation}"
    if _TASK_NAME.fullmatch(name) is None:
        raise ValueError("task_name_invalid")
    return name


def task_payload(job_id: uuid.UUID, generation: int) -> bytes:
    payload = json.dumps(
        {"generation": generation, "job_id": str(job_id)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(payload) > 128 or b"@" in payload or b"token" in payload.lower():
        raise ValueError("task_payload_invalid")
    return payload


def add_task_dispatch(session: Session, job: DurableJob, schedule_at: datetime, now: datetime) -> TaskDispatch:
    if schedule_at.tzinfo is None or schedule_at.utcoffset() is None:
        raise ValueError("task_schedule_must_be_aware")
    dispatch = TaskDispatch(
        job_id=job.id,
        generation=job.generation,
        task_name=deterministic_task_name(job.id, job.generation),
        schedule_at=schedule_at,
        state="pending",
        attempt_count=0,
        dispatched_at=None,
        safe_error_code=None,
        created_at=now,
        updated_at=now,
    )
    session.add(dispatch)
    return dispatch


class TaskCreator(Protocol):
    async def create(self, task_name: str, payload: bytes, schedule_at: datetime) -> None: ...


class CloudTasksCreator:
    def __init__(
        self,
        *,
        project: str,
        location: str,
        queue: str,
        worker_url: str,
        service_account_email: str,
        audience: str,
        client: tasks_v2.CloudTasksClient | None = None,
    ) -> None:
        self._client = client or tasks_v2.CloudTasksClient()
        self._parent = self._client.queue_path(project, location, queue)
        self._worker_url = worker_url
        self._service_account_email = service_account_email
        self._audience = audience

    async def create(self, task_name: str, payload: bytes, schedule_at: datetime) -> None:
        schedule = timestamp_pb2.Timestamp()
        schedule.FromDatetime(schedule_at.astimezone(UTC))
        task = tasks_v2.Task(
            name=f"{self._parent}/tasks/{task_name}",
            schedule_time=schedule,
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=self._worker_url,
                headers={"Content-Type": "application/json"},
                body=payload,
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._service_account_email,
                    audience=self._audience,
                ),
            ),
        )
        try:
            await asyncio.to_thread(self._client.create_task, parent=self._parent, task=task)
        except AlreadyExists:
            return


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    selected: int
    dispatched: int


class TaskDispatchCoordinator:
    def __init__(self, session_factory: sessionmaker[Session], creator: TaskCreator) -> None:
        self._session_factory = session_factory
        self._creator = creator

    @staticmethod
    def pending_query(limit: int, order_id: uuid.UUID | None = None) -> Select[tuple[TaskDispatch]]:
        statement = (
            select(TaskDispatch)
            .join(DurableJob, DurableJob.id == TaskDispatch.job_id)
            .where(TaskDispatch.state == "pending")
            .order_by(TaskDispatch.schedule_at, TaskDispatch.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        if order_id is not None:
            statement = statement.where(DurableJob.order_id == order_id)
        return statement

    async def dispatch(self, job_id: uuid.UUID, generation: int) -> bool:
        with self._session_factory() as session:
            record = session.scalar(
                select(TaskDispatch).where(
                    TaskDispatch.job_id == job_id,
                    TaskDispatch.generation == generation,
                )
            )
            if record is None or record.state == "dispatched":
                return False
            task_name = record.task_name
            schedule_at = _aware(record.schedule_at)
        try:
            await self._creator.create(task_name, task_payload(job_id, generation), schedule_at)
        except Exception as error:
            with self._session_factory() as session, session.begin():
                current = session.scalar(
                    select(TaskDispatch)
                    .where(TaskDispatch.job_id == job_id, TaskDispatch.generation == generation)
                    .with_for_update()
                )
                if current is not None and current.state == "pending":
                    current.attempt_count += 1
                    current.safe_error_code = "cloud_tasks_create_failed"
                    current.updated_at = datetime.now(UTC)
            raise TaskDispatchUnavailable("cloud_tasks_create_failed") from error
        with self._session_factory() as session, session.begin():
            current = session.scalar(
                select(TaskDispatch)
                .where(TaskDispatch.job_id == job_id, TaskDispatch.generation == generation)
                .with_for_update()
            )
            if current is None or current.state == "dispatched":
                return False
            now = datetime.now(UTC)
            current.state = "dispatched"
            current.attempt_count += 1
            current.dispatched_at = now
            current.safe_error_code = None
            current.updated_at = now
        return True

    async def reconcile(self, *, limit: int = 100, order_id: uuid.UUID | None = None) -> ReconcileResult:
        if not 1 <= limit <= 1000:
            raise ValueError("task_reconcile_limit_invalid")
        with self._session_factory() as session:
            selected = [
                (row.job_id, row.generation) for row in session.scalars(self.pending_query(limit, order_id)).all()
            ]
        dispatched = 0
        for job_id, generation in selected:
            dispatched += int(await self.dispatch(job_id, generation))
        return ReconcileResult(selected=len(selected), dispatched=dispatched)

    async def recover_stale_dispatched(
        self,
        *,
        stale_grace: timedelta,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ReconcileResult:
        if not 1 <= limit <= 1000:
            raise ValueError("task_reconcile_limit_invalid")
        if not MIN_STALE_DISPATCH_GRACE <= stale_grace <= MAX_STALE_DISPATCH_GRACE:
            raise ValueError("task_stale_grace_invalid")
        recovered_at = now or datetime.now(UTC)
        if recovered_at.tzinfo is None or recovered_at.utcoffset() is None:
            raise ValueError("task_recovery_now_must_be_aware")
        stale_before = recovered_at - stale_grace
        recovered: list[tuple[uuid.UUID, int]] = []
        with self._session_factory() as session, session.begin():
            statement = (
                select(DurableJob, TaskDispatch)
                .join(
                    TaskDispatch,
                    (TaskDispatch.job_id == DurableJob.id) & (TaskDispatch.generation == DurableJob.generation),
                )
                .where(
                    TaskDispatch.state == "dispatched",
                    TaskDispatch.schedule_at <= stale_before,
                    TaskDispatch.dispatched_at <= stale_before,
                    DurableJob.state.in_(("available", "retry", "leased")),
                    DurableJob.available_at <= stale_before,
                    DurableJob.attempt_count < DurableJob.max_attempts,
                    (DurableJob.lease_until.is_(None)) | (DurableJob.lease_until < recovered_at),
                )
                .order_by(DurableJob.available_at, TaskDispatch.dispatched_at)
                .with_for_update(of=DurableJob, skip_locked=True)
                .limit(limit)
            )
            for job, old_dispatch in session.execute(statement).all():
                old_dispatch.safe_error_code = "superseded_stale_dispatch"
                old_dispatch.updated_at = recovered_at
                if job.lease_owner is not None:
                    expired_attempt = session.scalar(
                        select(JobAttempt).where(
                            JobAttempt.job_id == job.id,
                            JobAttempt.attempt_number == job.attempt_count,
                            JobAttempt.finished_at.is_(None),
                        )
                    )
                    if expired_attempt is not None:
                        expired_attempt.finished_at = recovered_at
                        expired_attempt.outcome = "retry"
                        expired_attempt.safe_error_code = "lease_expired_recovered"
                    job.state = "available"
                    job.lease_owner = None
                    job.lease_until = None
                job.generation += 1
                job.updated_at = recovered_at
                add_task_dispatch(session, job, recovered_at, recovered_at)
                recovered.append((job.id, job.generation))
        dispatched = 0
        for job_id, generation in recovered:
            dispatched += int(await self.dispatch(job_id, generation))
        return ReconcileResult(selected=len(recovered), dispatched=dispatched)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
