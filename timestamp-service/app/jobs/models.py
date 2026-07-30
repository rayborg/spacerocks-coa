from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class JobState(StrEnum):
    AVAILABLE = "available"
    LEASED = "leased"
    RETRY = "retry"
    COMPLETE = "complete"
    MANUAL_REVIEW = "manual_review"
    DEAD_LETTER = "dead_letter"


class JobOutcome(StrEnum):
    COMPLETE = "complete"
    RETRY = "retry"
    MANUAL_REVIEW = "manual_review"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_key: str
    kind: str
    order_id: str
    max_attempts: int = 10

    def __post_init__(self) -> None:
        if not self.job_key or len(self.job_key) > 160 or self.max_attempts < 1:
            raise ValueError("invalid durable job specification")


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    spec: JobSpec
    attempt: int
    lease_owner: str
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    base_seconds: float = 30.0
    maximum_seconds: float = 21600.0
    jitter_ratio: float = 0.2

    def delay(self, attempt: int, jitter_unit: float) -> timedelta:
        if attempt < 1 or not 0.0 <= jitter_unit <= 1.0 or not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("invalid retry calculation inputs")
        exponential = min(self.maximum_seconds, self.base_seconds * (2 ** (attempt - 1)))
        factor = 1.0 - self.jitter_ratio + (2.0 * self.jitter_ratio * jitter_unit)
        return timedelta(seconds=max(0.0, exponential * factor))
