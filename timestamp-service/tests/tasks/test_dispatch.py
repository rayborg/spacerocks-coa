from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from google.api_core.exceptions import AlreadyExists
from sqlalchemy import create_engine, select

from app.db.base import Base
from app.db.models import DurableJob, JobAttempt, Order, TaskDispatch
from app.db.repositories import SqlJobClaimStore
from app.db.session import create_session_factory
from app.jobs.models import JobOutcome, JobSpec, SpecificJobRetryable
from app.tasks.dispatch import (
    MAX_STALE_DISPATCH_GRACE,
    MIN_STALE_DISPATCH_GRACE,
    CloudTasksCreator,
    TaskDispatchCoordinator,
    TaskDispatchUnavailable,
    add_task_dispatch,
    deterministic_task_name,
    task_payload,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


class RecordingCreator:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[str, bytes, datetime]] = []

    async def create(self, task_name: str, payload: bytes, schedule_at: datetime) -> None:
        self.calls.append((task_name, payload, schedule_at))
        if not self.available:
            raise OSError("dispatch unavailable")


def runtime():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    order_id = uuid.uuid4()
    with factory() as session, session.begin():
        session.add(
            Order(
                id=order_id,
                order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
                certificate_reference="TEST-001",
                manifest_digest=b"d" * 32,
                email="private@example.test",
                amount_minor=500,
                currency="usd",
                product_version="test",
                payment_mode="fixture",
                payment_state="paid",
                fulfillment_state="queued",
                consent_terms_version="v1",
                consent_privacy_version="v1",
                consent_accepted_at=NOW,
                checkout_session_id=None,
                payment_intent_id=None,
                fulfillment_key=f"stamp:{order_id}",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return engine, factory, order_id


def seed_dispatched(
    factory,
    order_id: uuid.UUID,
    *,
    suffix: str,
    state: str = "available",
    available_at: datetime | None = None,
    dispatched_at: datetime | None = None,
    lease_until: datetime | None = None,
    attempt_count: int = 0,
    max_attempts: int = 10,
) -> uuid.UUID:
    due_at = available_at or NOW - timedelta(hours=3)
    sent_at = dispatched_at or NOW - timedelta(hours=3)
    job = DurableJob(
        job_key=f"{suffix}:{order_id}",
        order_id=order_id,
        kind="stamp_manifest_digest",
        state=state,
        generation=1,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        available_at=due_at,
        lease_owner="worker-active" if lease_until is not None else None,
        lease_until=lease_until,
        safe_error_code=None,
        created_at=due_at,
        updated_at=due_at,
    )
    with factory() as session, session.begin():
        session.add(job)
        session.flush()
        dispatch = add_task_dispatch(session, job, due_at, due_at)
        dispatch.state = "dispatched"
        dispatch.attempt_count = 1
        dispatch.dispatched_at = sent_at
        dispatch.updated_at = sent_at
    return job.id


@pytest.mark.asyncio
async def test_commit_create_outage_is_recoverable_and_duplicate_enqueue_is_deterministic() -> None:
    engine, factory, order_id = runtime()
    creator = RecordingCreator(available=False)
    coordinator = TaskDispatchCoordinator(factory, creator)
    store = SqlJobClaimStore(factory, coordinator)
    spec = JobSpec(f"stamp:{order_id}", "stamp_manifest_digest", str(order_id))

    with pytest.raises(TaskDispatchUnavailable):
        await store.enqueue_once(spec, NOW)
    with factory() as session:
        job = session.scalar(select(DurableJob))
        dispatch = session.scalar(select(TaskDispatch))
        assert job is not None and dispatch is not None
        assert dispatch.job_id == job.id and dispatch.generation == job.generation == 1
        assert dispatch.state == "pending" and dispatch.attempt_count == 1
        assert dispatch.task_name == deterministic_task_name(job.id, 1)

    creator.available = True
    result = await coordinator.reconcile()
    assert result.selected == result.dispatched == 1
    assert not await store.enqueue_once(spec, NOW)
    assert len({name for name, _payload, _schedule in creator.calls}) == 1
    assert creator.calls[-1][1] == task_payload(job.id, 1)
    assert creator.calls[-1][1] == f'{{"generation":1,"job_id":"{job.id}"}}'.encode()
    engine.dispose()


@pytest.mark.asyncio
async def test_exact_claim_rejects_task_before_row_duplicates_early_and_stale_generations() -> None:
    engine, factory, order_id = runtime()
    store = SqlJobClaimStore(factory)
    missing = str(uuid.uuid4())
    with pytest.raises(SpecificJobRetryable, match="not_committed"):
        await store.claim_specific(missing, 1, "worker-a", NOW, timedelta(minutes=2))

    spec = JobSpec(f"stamp:{order_id}", "stamp_manifest_digest", str(order_id), max_attempts=3)
    assert await store.enqueue_once(spec, NOW)
    with factory() as session:
        job = session.scalar(select(DurableJob))
        assert job is not None
        job_id = str(job.id)
    first = await store.claim_specific(job_id, 1, "worker-a", NOW, timedelta(minutes=2))
    assert first is not None
    with pytest.raises(SpecificJobRetryable, match="lease_active"):
        await store.claim_specific(job_id, 1, "worker-b", NOW, timedelta(minutes=2))
    retry_at = NOW + timedelta(minutes=5)
    await store.finish(first, JobOutcome.RETRY, NOW, retry_at=retry_at, safe_error_code="temporary")
    assert await store.claim_specific(job_id, 1, "worker-b", retry_at, timedelta(minutes=2)) is None
    with pytest.raises(SpecificJobRetryable, match="not_due"):
        await store.claim_specific(job_id, 2, "worker-b", NOW, timedelta(minutes=2))
    second = await store.claim_specific(job_id, 2, "worker-b", retry_at, timedelta(minutes=2))
    assert second is not None and second.attempt == 2
    assert not await store.heartbeat(job_id, "worker-b", 1, retry_at + timedelta(minutes=4))
    assert await store.heartbeat(job_id, "worker-b", 2, retry_at + timedelta(minutes=4))
    with factory() as session:
        dispatches = session.scalars(select(TaskDispatch).order_by(TaskDispatch.generation)).all()
        assert [dispatch.generation for dispatch in dispatches] == [1, 2]
        assert dispatches[1].schedule_at.replace(tzinfo=UTC) == retry_at
    engine.dispose()


@pytest.mark.asyncio
async def test_cloud_tasks_adapter_uses_exact_oidc_http_task_and_accepts_already_exists() -> None:
    class ExistingClient:
        def __init__(self) -> None:
            self.task = None

        def queue_path(self, project: str, location: str, queue: str) -> str:
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def create_task(self, *, parent: str, task: object) -> None:
            assert parent == "projects/project/locations/location/queues/queue"
            self.task = task
            raise AlreadyExists("exists")

    client = ExistingClient()
    creator = CloudTasksCreator(
        project="project",
        location="location",
        queue="queue",
        worker_url="https://worker.example/internal/tasks/run",
        service_account_email="tasks@project.iam.gserviceaccount.com",
        audience="https://worker.example/",
        client=client,  # type: ignore[arg-type]
    )
    job_id = uuid.uuid4()
    await creator.create(deterministic_task_name(job_id, 1), task_payload(job_id, 1), NOW)
    assert client.task is not None
    request = client.task.http_request
    assert request.url == "https://worker.example/internal/tasks/run"
    assert bytes(request.body) == task_payload(job_id, 1)
    assert request.oidc_token.service_account_email == "tasks@project.iam.gserviceaccount.com"
    assert "private@example" not in repr(client.task)


@pytest.mark.asyncio
async def test_explicit_stale_recovery_increments_generation_and_invalidates_old_task() -> None:
    engine, factory, order_id = runtime()
    job_id = seed_dispatched(factory, order_id, suffix="lost")
    creator = RecordingCreator()
    coordinator = TaskDispatchCoordinator(factory, creator)

    assert (await coordinator.reconcile()).selected == 0
    result = await coordinator.recover_stale_dispatched(
        stale_grace=MIN_STALE_DISPATCH_GRACE,
        now=NOW,
    )
    assert result.selected == result.dispatched == 1
    assert creator.calls == [
        (deterministic_task_name(job_id, 2), task_payload(job_id, 2), NOW),
    ]
    with factory() as session:
        job = session.get(DurableJob, job_id)
        dispatches = session.scalars(
            select(TaskDispatch).where(TaskDispatch.job_id == job_id).order_by(TaskDispatch.generation)
        ).all()
        assert job is not None and job.generation == 2
        assert [dispatch.generation for dispatch in dispatches] == [1, 2]
        assert dispatches[0].safe_error_code == "superseded_stale_dispatch"
        assert dispatches[1].state == "dispatched"

    store = SqlJobClaimStore(factory)
    assert await store.claim_specific(str(job_id), 1, "old-task", NOW, timedelta(minutes=2)) is None
    claimed = await store.claim_specific(str(job_id), 2, "new-task", NOW, timedelta(minutes=2))
    assert claimed is not None
    engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_excludes_unsafe_jobs_and_enforces_grace_bounds() -> None:
    engine, factory, order_id = runtime()
    excluded = {
        seed_dispatched(factory, order_id, suffix="complete", state="complete"),
        seed_dispatched(factory, order_id, suffix="dead", state="dead_letter"),
        seed_dispatched(factory, order_id, suffix="manual", state="manual_review"),
        seed_dispatched(factory, order_id, suffix="future", available_at=NOW + timedelta(minutes=1)),
        seed_dispatched(factory, order_id, suffix="active", state="leased", lease_until=NOW + timedelta(minutes=1)),
        seed_dispatched(factory, order_id, suffix="young", dispatched_at=NOW - timedelta(minutes=1)),
        seed_dispatched(factory, order_id, suffix="exhausted", attempt_count=10, max_attempts=10),
    }
    creator = RecordingCreator()
    coordinator = TaskDispatchCoordinator(factory, creator)

    result = await coordinator.recover_stale_dispatched(
        stale_grace=MIN_STALE_DISPATCH_GRACE,
        now=NOW,
    )
    assert result.selected == result.dispatched == 0
    assert creator.calls == []
    with factory() as session:
        assert set(session.scalars(select(DurableJob.id).where(DurableJob.id.in_(excluded))).all()) == excluded
        assert set(session.scalars(select(DurableJob.generation).where(DurableJob.id.in_(excluded))).all()) == {1}

    with pytest.raises(ValueError, match="task_stale_grace_invalid"):
        await coordinator.recover_stale_dispatched(stale_grace=MIN_STALE_DISPATCH_GRACE - timedelta(seconds=1))
    with pytest.raises(ValueError, match="task_stale_grace_invalid"):
        await coordinator.recover_stale_dispatched(stale_grace=MAX_STALE_DISPATCH_GRACE + timedelta(seconds=1))
    engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_accepts_due_retry_and_expired_lease() -> None:
    engine, factory, order_id = runtime()
    retry_id = seed_dispatched(factory, order_id, suffix="retry", state="retry")
    expired_id = seed_dispatched(
        factory,
        order_id,
        suffix="expired",
        state="leased",
        lease_until=NOW - timedelta(seconds=1),
    )
    creator = RecordingCreator()
    coordinator = TaskDispatchCoordinator(factory, creator)

    result = await coordinator.recover_stale_dispatched(
        stale_grace=MIN_STALE_DISPATCH_GRACE,
        now=NOW,
    )
    assert result.selected == result.dispatched == 2
    with factory() as session:
        generations = session.execute(
            select(DurableJob.id, DurableJob.generation).where(DurableJob.id.in_({retry_id, expired_id}))
        ).all()
        assert set(generations) == {(retry_id, 2), (expired_id, 2)}
    engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_fences_expired_claim_and_preserves_attempt_history() -> None:
    engine, factory, order_id = runtime()
    job_id = seed_dispatched(factory, order_id, suffix="expired-claim")
    store = SqlJobClaimStore(factory)
    claim_at = NOW - timedelta(hours=1)
    old_claim = await store.claim_specific(str(job_id), 1, "old-worker", claim_at, timedelta(minutes=1))
    assert old_claim is not None

    creator = RecordingCreator()
    coordinator = TaskDispatchCoordinator(factory, creator)
    result = await coordinator.recover_stale_dispatched(
        stale_grace=MIN_STALE_DISPATCH_GRACE,
        now=NOW,
    )
    assert result.selected == result.dispatched == 1
    assert not await store.heartbeat(str(job_id), "old-worker", old_claim.attempt, NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="lease is no longer owned"):
        await store.finish(old_claim, JobOutcome.COMPLETE, NOW)

    replacement = await store.claim_specific(str(job_id), 2, "new-worker", NOW, timedelta(minutes=2))
    assert replacement is not None and replacement.attempt == 2
    await store.finish(replacement, JobOutcome.COMPLETE, NOW + timedelta(minutes=1))
    with factory() as session:
        job = session.get(DurableJob, job_id)
        attempts = session.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_number)
        ).all()
        assert job is not None and job.generation == 2 and job.state == "complete"
        assert job.lease_owner is None and job.lease_until is None
        assert [(attempt.attempt_number, attempt.outcome) for attempt in attempts] == [(1, "retry"), (2, "complete")]
        assert attempts[0].finished_at is not None
        assert attempts[0].safe_error_code == "lease_expired_recovered"
    engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_stale_recovery_creates_only_one_new_generation() -> None:
    class BlockingCreator(RecordingCreator):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def create(self, task_name: str, payload: bytes, schedule_at: datetime) -> None:
            await super().create(task_name, payload, schedule_at)
            self.started.set()
            await self.release.wait()

    engine, factory, order_id = runtime()
    job_id = seed_dispatched(factory, order_id, suffix="concurrent")
    creator = BlockingCreator()
    coordinator = TaskDispatchCoordinator(factory, creator)
    first = asyncio.create_task(coordinator.recover_stale_dispatched(stale_grace=MIN_STALE_DISPATCH_GRACE, now=NOW))
    await creator.started.wait()
    second = await coordinator.recover_stale_dispatched(stale_grace=MIN_STALE_DISPATCH_GRACE, now=NOW)
    creator.release.set()
    first_result = await first

    assert (first_result.selected, first_result.dispatched) == (1, 1)
    assert (second.selected, second.dispatched) == (0, 0)
    with factory() as session:
        job = session.get(DurableJob, job_id)
        dispatches = session.scalars(select(TaskDispatch).where(TaskDispatch.job_id == job_id)).all()
        assert job is not None and job.generation == 2
        assert sorted(dispatch.generation for dispatch in dispatches) == [1, 2]
    assert len(creator.calls) == 1
    engine.dispose()
