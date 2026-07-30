from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import REPOSITORY_ROOT
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.dialects import postgresql

from app.db.base import Base
from app.db.cleanup import cleanup_ephemeral_records
from app.db.models import (
    IdempotencyRequest,
    JobAttempt,
    Order,
    OrderToken,
    ProofVersion,
    RateCounter,
    StateEvent,
)
from app.db.repositories import OrderStore, RateLimitStore, SqlJobClaimStore
from app.db.session import create_session_factory
from app.jobs.models import JobOutcome, JobSpec


def _order(now: datetime) -> Order:
    order_id = uuid.uuid4()
    return Order(
        id=order_id,
        order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
        certificate_reference="TEST-001",
        manifest_digest=b"d" * 32,
        email="customer@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        payment_state="paid",
        fulfillment_state="queued",
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=now,
        checkout_session_id="cs_test_repo",
        payment_intent_id="pi_test_repo",
        fulfillment_key=f"stamp:{order_id}",
        created_at=now,
        updated_at=now,
    )


def test_rate_limit_counter_is_atomic_and_never_stores_raw_ip() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    store = RateLimitStore(factory, b"p" * 32)
    now = datetime.now(UTC)
    assert store.hit("checkout", "192.0.2.44", now, 2)
    assert store.hit("checkout", "192.0.2.44", now, 2)
    assert not store.hit("checkout", "192.0.2.44", now, 2)
    with factory() as session:
        counter = session.scalar(select(RateCounter))
        assert counter is not None
        assert counter.request_count == 3
        assert len(counter.key_hash) == 32
        assert b"192.0.2.44" not in counter.key_hash


@pytest.mark.asyncio
async def test_durable_job_claim_retry_and_attempt_history() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    order = _order(now)
    with factory() as session, session.begin():
        session.add(order)
    store = SqlJobClaimStore(factory)
    spec = JobSpec(job_key=order.fulfillment_key, kind="stamp_manifest_digest", order_id=str(order.id), max_attempts=2)
    assert await store.enqueue_once(spec, now)
    assert not await store.enqueue_once(spec, now)
    claim = await store.claim("worker-1", now, timedelta(minutes=5))
    assert claim is not None and claim.attempt == 1
    retry_at = now + timedelta(minutes=1)
    await store.finish(claim, JobOutcome.RETRY, now, retry_at=retry_at, safe_error_code="calendar_unavailable")
    assert await store.claim("worker-2", now, timedelta(minutes=5)) is None
    second = await store.claim("worker-2", retry_at, timedelta(minutes=5))
    assert second is not None and second.attempt == 2
    await store.finish(second, JobOutcome.RETRY, retry_at, retry_at=retry_at + timedelta(minutes=1))
    with factory() as session:
        attempts = session.scalars(select(JobAttempt).order_by(JobAttempt.attempt_number)).all()
        assert [attempt.outcome for attempt in attempts] == ["retry", "dead_letter"]


def test_postgres_claim_statement_uses_skip_locked() -> None:
    statement = SqlJobClaimStore.claim_query(datetime.now(UTC))
    rendered = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE SKIP LOCKED" in rendered


def test_postgres_checkout_credential_claim_uses_row_lock() -> None:
    statement = OrderStore.idempotency_query("POST:/v1/checkout", b"k" * 32)
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in rendered


def test_cleanup_removes_only_explicitly_expired_ephemeral_records() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    order = _order(now)
    with factory() as session, session.begin():
        session.add(order)
        session.flush()
        session.add_all(
            [
                OrderToken(
                    order_id=order.id,
                    version=1,
                    pepper_version=1,
                    token_hash=b"t" * 32,
                    revoked_at=old,
                    expires_at=old,
                    created_at=old,
                ),
                IdempotencyRequest(
                    endpoint="POST:/v1/checkout",
                    key_hash=b"k" * 32,
                    request_hash=b"r" * 32,
                    order_id=order.id,
                    provider_idempotency_key="provider-key",
                    provider_price_id="fixture_price",
                    success_url="https://example.test/success",
                    cancel_url="https://example.test/cancel",
                    checkout_state="completed",
                    checkout_lease_id=str(uuid.uuid4()),
                    checkout_lease_expires_at=old,
                    response_status=201,
                    response_body={"order_reference": order.order_reference},
                    completed_at=old,
                    created_at=old,
                ),
                RateCounter(
                    endpoint="checkout",
                    key_hash=b"c" * 32,
                    window_started_at=old,
                    request_count=1,
                ),
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"proof",
                    proof_sha256=hashlib.sha256(b"proof").digest(),
                    proof_byte_length=5,
                    proof_state="calendar_pending",
                    calendar_submitted_at=now,
                    verification_metadata=None,
                    created_at=now,
                ),
                StateEvent(
                    order_id=order.id,
                    sequence=1,
                    event_key="durable-evidence",
                    source="test",
                    previous_payment_state="checkout_open",
                    payment_state="paid",
                    previous_fulfillment_state="awaiting_payment",
                    fulfillment_state="queued",
                    evidence={"durable": True},
                    created_at=now,
                ),
            ]
        )
    result = cleanup_ephemeral_records(
        factory,
        expired_token_before=now,
        idempotency_before=now,
        rate_counter_before=now,
    )
    assert result.expired_tokens == result.idempotency_records == result.rate_counters == 1
    with factory() as session:
        assert session.scalar(select(Order)) is not None
        assert session.scalar(select(ProofVersion)) is not None
        assert session.scalar(select(StateEvent)) is not None
        assert session.scalar(select(OrderToken)) is None
        assert session.scalar(select(IdempotencyRequest)) is None
        assert session.scalar(select(RateCounter)) is None


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured")
def test_postgres_concurrent_checkout_returns_one_recoverable_credential(
    app_factory: Any,
) -> None:
    context = app_factory(sqlite_url=os.environ["TEST_POSTGRES_URL"])
    payload = json.loads(
        (REPOSITORY_ROOT / "contracts/fixtures/checkout-request.valid.json").read_text(encoding="utf-8")
    )
    key = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")

    def submit() -> tuple[int, dict[str, Any]]:
        with TestClient(context.app) as client:
            response = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})
            return response.status_code, response.json()

    order_reference: str | None = None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: submit(), range(2)))
        assert sorted(status for status, _body in responses) == [201, 425]
        success = next(body for status, body in responses if status == 201)
        order_reference = str(success["order_reference"])
        with TestClient(context.app) as client:
            assert client.get(
                "/v1/orders/status",
                headers={"Authorization": f"Bearer {success['status_token']}"},
            ).status_code == 200
            with context.session_factory() as session, session.begin():
                reservation = session.scalar(
                    select(IdempotencyRequest)
                    .join(Order, Order.id == IdempotencyRequest.order_id)
                    .where(Order.order_reference == order_reference)
                    .with_for_update()
                )
                assert reservation is not None
                reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            recovered = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})
            assert recovered.status_code == 201
            assert client.get(
                "/v1/orders/status",
                headers={"Authorization": f"Bearer {success['status_token']}"},
            ).status_code == 401
            assert client.get(
                "/v1/orders/status",
                headers={"Authorization": f"Bearer {recovered.json()['status_token']}"},
            ).status_code == 200
    finally:
        if order_reference is not None:
            with context.session_factory() as session, session.begin():
                session.execute(text("ALTER TABLE state_events DISABLE TRIGGER state_events_append_only"))
                session.execute(delete(Order).where(Order.order_reference == order_reference))
                session.execute(text("ALTER TABLE state_events ENABLE TRIGGER state_events_append_only"))
