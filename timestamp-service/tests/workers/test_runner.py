from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.fulfillment.errors import RetryableFulfillmentError
from app.jobs.models import ClaimedJob, JobOutcome, JobSpec
from app.worker.runner import Worker, WorkerSettings


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 30, 12, tzinfo=UTC)


class Random:
    def bytes(self, length: int) -> bytes:
        return b"x" * length

    def uniform(self, lower: float, upper: float) -> float:
        assert (lower, upper) == (0.0, 1.0)
        return 0.5


class Claims:
    def __init__(self, attempt: int, max_attempts: int = 3) -> None:
        self.job = ClaimedJob(
            id="job_opaque",
            spec=JobSpec("job-key", "stamp", "order_opaque", max_attempts),
            attempt=attempt,
            lease_owner="worker_opaque",
            lease_until=Clock().now() + timedelta(minutes=2),
        )
        self.finished: list[tuple[JobOutcome, datetime | None, str | None]] = []
        self.claimed = False
        self.heartbeat_count = 0
        self.heartbeat_result = True

    async def enqueue_once(self, spec: JobSpec, available_at: datetime) -> bool:
        del spec, available_at
        return True

    async def claim(self, worker_id: str, now: datetime, lease_for: timedelta) -> ClaimedJob | None:
        assert worker_id == "worker_opaque"
        del now, lease_for
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    async def heartbeat(self, job_id: str, worker_id: str, lease_until: datetime) -> bool:
        del job_id, worker_id, lease_until
        self.heartbeat_count += 1
        return self.heartbeat_result

    async def finish(
        self,
        job: ClaimedJob,
        outcome: JobOutcome,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error_code: str | None = None,
    ) -> None:
        del job, now
        self.finished.append((outcome, retry_at, safe_error_code))


class Terminal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, order_id: str, safe_code: str) -> None:
        self.calls.append((order_id, safe_code))


@pytest.mark.asyncio
async def test_retry_uses_bounded_backoff_and_duplicate_claim_is_not_processed() -> None:
    claims = Claims(attempt=1)

    async def retry(_order_id: str) -> None:
        raise RetryableFulfillmentError("calendar_submit_unavailable")

    worker = Worker(
        worker_id="worker_opaque",
        claims=claims,
        handlers={"stamp": retry},
        terminal_failure=Terminal(),
        clock=Clock(),
        random=Random(),
    )
    assert await worker.run_once()
    assert not await worker.run_once()
    outcome, retry_at, code = claims.finished[0]
    assert outcome == JobOutcome.RETRY
    assert retry_at == Clock().now() + timedelta(seconds=30)
    assert code == "calendar_submit_unavailable"


@pytest.mark.asyncio
async def test_retry_exhaustion_dead_letters_without_exception_details() -> None:
    claims = Claims(attempt=3, max_attempts=3)
    terminal = Terminal()

    async def fail(_order_id: str) -> None:
        raise RuntimeError("secret must not enter job history")

    worker = Worker(
        worker_id="worker_opaque",
        claims=claims,
        handlers={"stamp": fail},
        terminal_failure=terminal,
        clock=Clock(),
        random=Random(),
    )
    assert await worker.run_once()
    assert claims.finished == [(JobOutcome.DEAD_LETTER, None, "job_unexpected_failure")]
    assert terminal.calls == [("order_opaque", "job_unexpected_failure")]


@pytest.mark.asyncio
async def test_long_job_heartbeats_lease() -> None:
    claims = Claims(attempt=1)

    async def long_job(_order_id: str) -> None:
        await asyncio.sleep(1.05)

    worker = Worker(
        worker_id="worker_opaque",
        claims=claims,
        handlers={"stamp": long_job},
        terminal_failure=Terminal(),
        clock=Clock(),
        random=Random(),
        settings=WorkerSettings(lease_for=timedelta(seconds=12), heartbeat_every_seconds=1),
    )
    assert await worker.run_once()
    assert claims.heartbeat_count >= 2
    assert claims.finished[0][0] == JobOutcome.COMPLETE


@pytest.mark.asyncio
async def test_lease_loss_cancels_handler_and_never_finishes_stale_job() -> None:
    claims = Claims(attempt=1)
    claims.heartbeat_result = False
    started = asyncio.Event()
    cancelled = asyncio.Event()
    persisted = False

    async def handler(_order_id: str) -> None:
        nonlocal persisted
        started.set()
        try:
            await asyncio.sleep(10)
            persisted = True
        finally:
            cancelled.set()

    worker = Worker(
        worker_id="worker_opaque",
        claims=claims,
        handlers={"stamp": handler},
        terminal_failure=Terminal(),
        clock=Clock(),
        random=Random(),
        settings=WorkerSettings(lease_for=timedelta(seconds=12), heartbeat_every_seconds=1),
    )
    assert await worker.run_once()
    assert started.is_set() and cancelled.is_set()
    assert not persisted
    assert claims.finished == []
