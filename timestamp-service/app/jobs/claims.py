from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.jobs.models import ClaimedJob, JobOutcome, JobSpec


class JobClaimStore(Protocol):
    async def enqueue_once(self, spec: JobSpec, available_at: datetime) -> bool: ...

    async def claim(self, worker_id: str, now: datetime, lease_for: timedelta) -> ClaimedJob | None:
        """Atomically claim with PostgreSQL row locking/skip-locked or equivalent lease semantics."""
        ...

    async def claim_specific(
        self,
        job_id: str,
        generation: int,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ClaimedJob | None: ...

    async def heartbeat(self, job_id: str, worker_id: str, attempt: int, lease_until: datetime) -> bool: ...

    async def finish(
        self,
        job: ClaimedJob,
        outcome: JobOutcome,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error_code: str | None = None,
    ) -> None: ...
