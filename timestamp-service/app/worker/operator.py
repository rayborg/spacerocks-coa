from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import AppEnvironment, PaymentMode, Settings
from app.db.fulfillment_adapters import SqlFulfillmentAdapters, create_sql_fulfillment_adapters
from app.db.models import DurableJob, JobAttempt, Order
from app.db.models import OutboxMessage as OutboxRecord
from app.db.session import create_database_engine, create_session_factory
from app.domain.order import FulfillmentState
from app.jobs.models import JobSpec, JobState
from app.tasks.composition import create_task_dispatch
from app.tasks.dispatch import TaskDispatchCoordinator, add_task_dispatch
from app.timestamping.detached import validate_exact_proof
from app.worker.composition import UPGRADE_JOB

_REVERIFICATION_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class SqlOperatorCommands:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        adapters: SqlFulfillmentAdapters,
        task_dispatch: TaskDispatchCoordinator | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._adapters = adapters
        self._task_dispatch = task_dispatch

    async def replay(self, job_id: str) -> None:
        now = datetime.now(UTC)
        terminal_order_id: str | None = None
        dispatch: tuple[uuid.UUID, int] | None = None
        with self._session_factory() as session, session.begin():
            job = session.scalar(select(DurableJob).where(DurableJob.id == _uuid(job_id)).with_for_update())
            if job is None:
                raise ValueError("job_not_found")
            if job.state in {JobState.MANUAL_REVIEW.value, JobState.DEAD_LETTER.value}:
                terminal_order_id = str(job.order_id)
            elif job.state != JobState.RETRY.value:
                raise ValueError("job_not_replayable")
            else:
                job.state = JobState.AVAILABLE.value
                job.available_at = now
                job.lease_owner = None
                job.lease_until = None
                job.safe_error_code = None
                job.max_attempts = max(job.max_attempts, job.attempt_count + 10)
                job.generation += 1
                job.updated_at = now
                add_task_dispatch(session, job, now, now)
                dispatch = (job.id, job.generation)
        if dispatch is not None and self._task_dispatch is not None:
            await self._task_dispatch.dispatch(*dispatch)
        if terminal_order_id is not None:
            await self._validate_terminal_recovery(terminal_order_id)
            raise RuntimeError("terminal_recovery_requires_ws1_state_transition")

    async def reverify(self, order_id: str, request_id: str | None = None) -> None:
        if request_id is not None and not _REVERIFICATION_REQUEST_ID.fullmatch(request_id):
            raise ValueError("reverification_request_id_invalid")
        await self._enqueue_proof_job(
            order_id,
            prefix="reverify",
            allowed={
                FulfillmentState.BITCOIN_VERIFIED,
                FulfillmentState.DELIVERED,
            },
            request_id=request_id,
        )

    async def upgrade(self, order_id: str) -> None:
        await self._enqueue_proof_job(
            order_id,
            prefix="upgrade-manual",
            allowed={FulfillmentState.CALENDAR_PENDING},
        )

    async def purge_synthetic(self, order_id: str, *, preserve_proofs: bool) -> None:
        if (
            not preserve_proofs
            or self._settings.app_env != AppEnvironment.TEST
            or self._settings.payment_mode != PaymentMode.FIXTURE
        ):
            raise RuntimeError("synthetic_purge_forbidden")
        order_uuid = _uuid(order_id)
        with self._session_factory() as session, session.begin():
            order = session.get(Order, order_uuid)
            if order is None or order.payment_mode != PaymentMode.FIXTURE.value:
                raise ValueError("synthetic_order_not_found")
            job_ids = select(DurableJob.id).where(DurableJob.order_id == order_uuid)
            session.execute(delete(JobAttempt).where(JobAttempt.job_id.in_(job_ids)))
            session.execute(delete(DurableJob).where(DurableJob.order_id == order_uuid))
            session.execute(delete(OutboxRecord).where(OutboxRecord.order_id == order_uuid))

    async def reconcile_tasks(self, limit: int) -> tuple[int, int]:
        if self._task_dispatch is None:
            raise RuntimeError("task_dispatch_disabled")
        result = await self._task_dispatch.reconcile(limit=limit)
        return result.selected, result.dispatched

    async def recover_stale_tasks(self, limit: int, stale_grace: timedelta) -> tuple[int, int]:
        if self._task_dispatch is None:
            raise RuntimeError("task_dispatch_disabled")
        result = await self._task_dispatch.recover_stale_dispatched(limit=limit, stale_grace=stale_grace)
        return result.selected, result.dispatched

    async def _enqueue_proof_job(
        self,
        order_id: str,
        *,
        prefix: str,
        allowed: set[FulfillmentState],
        request_id: str | None = None,
    ) -> None:
        order = await self._adapters.orders.get_for_fulfillment(order_id)
        if order is None:
            raise ValueError("order_not_found")
        if order.state.fulfillment not in allowed:
            raise ValueError("order_not_eligible")
        proof = await self._adapters.proofs.latest(order.state.snapshot.order_reference)
        if proof is None:
            raise ValueError("proof_not_found")
        request_key = request_id or "default"
        await self._adapters.jobs.enqueue_once(
            JobSpec(
                job_key=f"{prefix}:{order_id}:v{proof.version}:{request_key}",
                kind=UPGRADE_JOB,
                order_id=order_id,
            ),
            datetime.now(UTC),
        )

    async def _validate_terminal_recovery(self, order_id: str) -> None:
        order = await self._adapters.orders.get_for_fulfillment(order_id)
        if order is None or order.state.fulfillment != FulfillmentState.MANUAL_REVIEW:
            raise ValueError("terminal_recovery_order_state_invalid")
        proof = await self._adapters.proofs.latest(order.state.snapshot.order_reference)
        if proof is None:
            raise ValueError("terminal_recovery_proof_missing")
        if proof.target_digest != order.state.snapshot.manifest_digest:
            raise ValueError("terminal_recovery_target_mismatch")
        if order.calendar_submitted_at is None or proof.calendar_submitted_at != order.calendar_submitted_at:
            raise ValueError("terminal_recovery_calendar_time_mismatch")
        validate_exact_proof(
            order.state.snapshot.manifest_digest,
            proof.proof_bytes,
            expected_sha256=proof.proof_sha256,
        )


def create_operator_commands() -> SqlOperatorCommands:
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("operator_database_required")
    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    task_dispatch = create_task_dispatch(settings, session_factory)
    return SqlOperatorCommands(
        settings,
        session_factory,
        create_sql_fulfillment_adapters(session_factory, task_dispatch),
        task_dispatch,
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError("identifier_must_be_uuid") from error
