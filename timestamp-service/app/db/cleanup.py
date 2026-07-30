from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IdempotencyRequest, OrderToken, RateCounter


@dataclass(frozen=True, slots=True)
class CleanupResult:
    expired_tokens: int
    idempotency_records: int
    rate_counters: int


def cleanup_ephemeral_records(
    session_factory: sessionmaker[Session],
    *,
    expired_token_before: datetime,
    idempotency_before: datetime,
    rate_counter_before: datetime,
) -> CleanupResult:
    """Apply operator-selected cutoffs without touching orders or fulfillment evidence."""
    with session_factory() as session, session.begin():
        token_result = session.execute(
            delete(OrderToken).where(OrderToken.expires_at < expired_token_before)
        )
        idempotency_result = session.execute(
            delete(IdempotencyRequest).where(IdempotencyRequest.created_at < idempotency_before)
        )
        rate_result = session.execute(
            delete(RateCounter).where(RateCounter.window_started_at < rate_counter_before)
        )
        return CleanupResult(
            expired_tokens=token_result.rowcount or 0,
            idempotency_records=idempotency_result.rowcount or 0,
            rate_counters=rate_result.rowcount or 0,
        )
