from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from app.fulfillment.errors import ManualReviewError, RetryableFulfillmentError
from app.jobs.claims import JobClaimStore
from app.jobs.models import BackoffPolicy, ClaimedJob, JobOutcome
from app.ports.system import Clock, RandomSource

JobHandler = Callable[[str], Awaitable[None]]
TerminalHandler = Callable[[str, str], Awaitable[None]]
_OPAQUE_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _safe_log_value(value: str) -> str:
    return value if _OPAQUE_WORKER_ID.fullmatch(value) else "invalid"


async def _wait_for_event(event: asyncio.Event) -> None:
    await event.wait()


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    lease_for: timedelta = timedelta(minutes=2)
    heartbeat_every_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.lease_for <= timedelta(seconds=10):
            raise ValueError("worker_lease_too_short")
        if not 1 <= self.heartbeat_every_seconds < self.lease_for.total_seconds() / 2:
            raise ValueError("worker_heartbeat_interval_invalid")


class Worker:
    def __init__(
        self,
        *,
        worker_id: str,
        claims: JobClaimStore,
        handlers: Mapping[str, JobHandler],
        terminal_failure: TerminalHandler,
        clock: Clock,
        random: RandomSource,
        settings: WorkerSettings | None = None,
        backoff: BackoffPolicy | None = None,
    ) -> None:
        if not _OPAQUE_WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker_id_must_be_opaque")
        self._worker_id = worker_id
        self._claims = claims
        self._handlers = dict(handlers)
        self._terminal_failure = terminal_failure
        self._clock = clock
        self._random = random
        self._settings = settings or WorkerSettings()
        self._backoff = backoff or BackoffPolicy()
        self._log = logging.getLogger("timestamp.worker")

    async def run_once(self) -> bool:
        now = self._clock.now()
        job = await self._claims.claim(self._worker_id, now, self._settings.lease_for)
        if job is None:
            return False
        await self._run_claimed(job)
        return True

    async def run_specific(self, job_id: str, generation: int) -> bool:
        job = await self._claims.claim_specific(
            job_id,
            generation,
            self._worker_id,
            self._clock.now(),
            self._settings.lease_for,
        )
        if job is None:
            return False
        await self._run_claimed(job)
        return True

    async def _run_claimed(self, job: ClaimedJob) -> None:
        safe_job_id = _safe_log_value(job.id)
        self._log.info("event=job_claimed job_id=%s", safe_job_id)
        handler = self._handlers.get(job.spec.kind)
        if handler is None:
            await self._terminal_failure(job.spec.order_id, "job_kind_unknown")
            await self._finish(job, JobOutcome.MANUAL_REVIEW, "job_kind_unknown")
            return

        stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job, stop, lease_lost))
        handler_task: asyncio.Future[None] = asyncio.ensure_future(handler(job.spec.order_id))
        lease_waiter = asyncio.create_task(_wait_for_event(lease_lost))
        try:
            done, _pending = await asyncio.wait(
                {handler_task, lease_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_waiter in done and lease_lost.is_set():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                self._log.warning("event=lease_lost job_id=%s", safe_job_id)
                return
            lease_waiter.cancel()
            error: BaseException | None = None
            try:
                await handler_task
            except BaseException as exc:
                error = exc
            stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if lease_lost.is_set() or not await self._claims.heartbeat(
                job.id,
                self._worker_id,
                job.attempt,
                self._clock.now() + self._settings.lease_for,
            ):
                self._log.warning("event=lease_lost job_id=%s", safe_job_id)
                return
            if error is None:
                await self._finish(job, JobOutcome.COMPLETE, None)
            elif isinstance(error, ManualReviewError):
                await self._terminal_failure(job.spec.order_id, error.safe_code)
                await self._finish(job, JobOutcome.MANUAL_REVIEW, error.safe_code)
            elif isinstance(error, RetryableFulfillmentError):
                await self._retry_or_exhaust(job, error.safe_code)
            elif isinstance(error, asyncio.CancelledError):
                raise error
            else:
                await self._retry_or_exhaust(job, "job_unexpected_failure")
        finally:
            stop.set()
            lease_waiter.cancel()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, lease_waiter, return_exceptions=True)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, job: ClaimedJob, stop: asyncio.Event, lease_lost: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.heartbeat_every_seconds)
                return
            except TimeoutError:
                lease_until = self._clock.now() + self._settings.lease_for
                if not await self._claims.heartbeat(job.id, self._worker_id, job.attempt, lease_until):
                    lease_lost.set()
                    return

    async def _retry_or_exhaust(self, job: ClaimedJob, safe_code: str) -> None:
        if job.attempt >= job.spec.max_attempts:
            await self._terminal_failure(job.spec.order_id, safe_code)
            await self._finish(job, JobOutcome.DEAD_LETTER, safe_code)
            return
        jitter = self._random.uniform(0.0, 1.0)
        retry_at = self._clock.now() + self._backoff.delay(job.attempt, jitter)
        await self._claims.finish(job, JobOutcome.RETRY, self._clock.now(), retry_at, safe_code)
        self._log.warning(
            "event=job_retry job_id=%s code=%s",
            _safe_log_value(job.id),
            _safe_log_value(safe_code),
        )

    async def _finish(self, job: ClaimedJob, outcome: JobOutcome, safe_code: str | None) -> None:
        await self._claims.finish(job, outcome, self._clock.now(), safe_error_code=safe_code)
        self._log.info(
            "event=job_finished job_id=%s outcome=%s code=%s",
            _safe_log_value(job.id),
            outcome,
            _safe_log_value(safe_code) if safe_code else "none",
        )

    async def run_forever(self, *, idle_seconds: float = 1.0) -> None:
        if not 0.05 <= idle_seconds <= 60:
            raise ValueError("worker_idle_interval_invalid")
        while True:
            handled = await self.run_once()
            if not handled:
                await asyncio.sleep(idle_seconds)
