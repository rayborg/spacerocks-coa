from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    BitcoinConfirmationObservation,
    NotificationAttempt,
    Order,
    ProofBundle,
    ProofVerification,
    ProofVersion,
    ResendWebhookEvent,
    StateEvent,
)
from app.db.models import OutboxMessage as OutboxRecord
from app.db.notification_adapters import (
    SqlBitcoinConfirmationObservations,
    SqlNotificationOutbox,
    SqlResendWebhookStore,
)
from app.db.session import create_session_factory
from app.domain.identifiers import OrderReference
from app.ports.bitcoin import BitcoinVerification
from app.ports.notifications import (
    NotificationKind,
    ProviderAcceptance,
    VerifiedResendEvent,
    notification_message,
)

ORDER_REFERENCE = OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB")
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_offline_delivery_migration_contains_observation_and_legacy_conversion() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql+psycopg://phase0:unused@127.0.0.1:1/phase0"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=service_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "CREATE TABLE bitcoin_confirmation_observations" in result.stdout
    assert "INSERT INTO bitcoin_confirmation_observations" in result.stdout
    assert "confirmation_observation_id = observation.id" in result.stdout
    assert "accepted_at = COALESCE(delivered_at, created_at)" in result.stdout
    assert "WHERE sender.kind = 'timestamp-complete'" in result.stdout
    assert "legacy_evidence_missing" in result.stdout
    assert "legacy_delivery_without_signed_resend_evidence" in result.stdout
    assert "migration-20260827-legacy-delivery-manual-review" in result.stdout
    assert "NULL-legacy" not in result.stdout
    assert "NULL-downgrade" not in result.stdout
    assert "current_observation.observed_confirmations >= 1" in result.stdout


@pytest.fixture
def persistence():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    proof_bytes = b"verified-proof"
    bundle_bytes = b"PK\x03\x04verified-bundle"
    order = Order(
        order_reference=ORDER_REFERENCE.value,
        certificate_reference="PRIVATE-CERTIFICATE",
        manifest_digest=b"d" * 32,
        email="private@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        payment_state="paid",
        fulfillment_state="bitcoin_verified",
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=NOW,
        checkout_session_id="cs_test_notifications",
        payment_intent_id="pi_test_notifications",
        fulfillment_key="stamp:notification-test",
        calendar_submitted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    with factory() as session, session.begin():
        session.add(order)
        session.flush()
        session.add_all(
            [
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=proof_bytes,
                    proof_sha256=hashlib.sha256(proof_bytes).digest(),
                    proof_byte_length=len(proof_bytes),
                    proof_state="calendar_pending",
                    calendar_submitted_at=NOW,
                    verification_metadata=None,
                    created_at=NOW,
                ),
                ProofVerification(
                    order_id=order.id,
                    proof_version=1,
                    method="fixture-exact-digest",
                    verified_at=NOW,
                    block_height=900000,
                    block_hash="ab" * 32,
                    block_time=NOW,
                    confirmation_policy="at-least-one-confirmation",
                    created_at=NOW,
                ),
                ProofBundle(
                    order_id=order.id,
                    proof_version=1,
                    bundle_bytes=bundle_bytes,
                    bundle_sha256=hashlib.sha256(bundle_bytes).digest(),
                    bundle_byte_length=len(bundle_bytes),
                    created_at=NOW,
                ),
            ]
        )
    yield factory, order.id
    engine.dispose()


async def enqueue_and_accept(outbox, kind, provider_id, confirmations):
    factory = outbox.session_factory
    with factory() as session:
        order_id = session.scalar(select(Order.id).where(Order.order_reference == ORDER_REFERENCE.value))
    assert order_id is not None
    await record_observation(factory, order_id, confirmations)
    await outbox.enqueue(
        notification_message(
            kind,
            ORDER_REFERENCE,
            "private@example.test",
            proof_version=1,
            confirmation_count=confirmations,
        )
    )
    claim_time = datetime.now(UTC)
    claim = await outbox.claim("worker-a", claim_time, timedelta(minutes=1))
    assert claim is not None
    await outbox.record_accepted(claim, ProviderAcceptance(provider_id, 200), claim_time)
    return claim


def observed_verification(confirmations: int, *, observed_at: datetime | None = None) -> BitcoinVerification:
    return BitcoinVerification(
        verified=True,
        method="fixture-exact-digest",
        verified_at=observed_at or NOW + timedelta(seconds=confirmations),
        block_height=900000,
        block_hash="ab" * 32,
        block_time=NOW,
        confirmation_policy="at-least-one-confirmation",
        confirmations=confirmations,
    )


async def record_observation(factory, order_id, confirmations, *, observed_at=None):
    repository = SqlBitcoinConfirmationObservations(factory)
    return await repository.record_once(
        str(order_id),
        1,
        f"bitcoin-confirmations-{confirmations}-{int((observed_at or NOW).timestamp())}",
        observed_verification(confirmations, observed_at=observed_at),
    )


def provider_event(event_id: str, event_type: str, provider_id: str) -> VerifiedResendEvent:
    return VerifiedResendEvent(event_id, event_type, provider_id, NOW)


@pytest.mark.asyncio
async def test_confirmation_observations_are_append_only_current_evidence(persistence) -> None:
    factory, order_id = persistence
    repository = SqlBitcoinConfirmationObservations(factory)
    verification = observed_verification(1)
    first = await repository.record_once(str(order_id), 1, "observation-event-1", verification)
    replay = await repository.record_once(str(order_id), 1, "observation-event-1", verification)
    assert replay == first
    assert await repository.latest(str(order_id), 1) == first
    with pytest.raises(ValueError, match="event_conflict"):
        await repository.record_once(
            str(order_id),
            1,
            "observation-event-1",
            observed_verification(2),
        )
    with pytest.raises(ValueError, match="append-only"):
        with factory() as session, session.begin():
            stored = session.scalar(select(BitcoinConfirmationObservation))
            assert stored is not None
            stored.observed_confirmations = 2


@pytest.mark.asyncio
async def test_claim_lease_attempts_and_provider_acceptance_are_durable(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    await record_observation(factory, order_id, 1)
    await outbox.enqueue(
        notification_message(
            NotificationKind.INITIAL_CONFIRMATION,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            1,
        )
    )
    claim_time = datetime.now(UTC)
    first = await outbox.claim("worker-a", claim_time, timedelta(seconds=30))
    assert first is not None
    assert await outbox.claim("worker-b", claim_time, timedelta(seconds=30)) is None
    with pytest.raises(ValueError, match="lease_stale"):
        await outbox.record_accepted(
            first,
            ProviderAcceptance("provider-stale", 200),
            claim_time + timedelta(seconds=31),
        )

    reclaimed = await outbox.claim("worker-b", claim_time + timedelta(seconds=31), timedelta(seconds=30))
    assert reclaimed is not None and reclaimed.attempt == 2
    assert reclaimed.idempotency_key == first.idempotency_key
    await outbox.record_accepted(
        reclaimed,
        ProviderAcceptance("provider-accepted", 200),
        claim_time + timedelta(seconds=32),
    )
    assert await outbox.claim("worker-c", claim_time + timedelta(seconds=33), timedelta(seconds=30)) is None
    with factory() as session:
        attempts = session.scalars(select(NotificationAttempt).order_by(NotificationAttempt.attempt_number)).all()
        assert [attempt.outcome for attempt in attempts] == ["lease_expired", "accepted"]
        record = session.scalar(select(OutboxRecord))
        assert record is not None and record.state == "accepted" and record.delivered_at is None


@pytest.mark.asyncio
async def test_retry_is_bounded_by_idempotency_horizon(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory, idempotency_horizon=timedelta(minutes=1))
    await record_observation(factory, order_id, 1)
    await outbox.enqueue(
        notification_message(
            NotificationKind.INITIAL_CONFIRMATION,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            1,
        )
    )
    claim_time = datetime.now(UTC)
    claim = await outbox.claim("worker-a", claim_time, timedelta(seconds=30))
    assert claim is not None
    await outbox.record_retry(
        claim,
        claim_time,
        claim_time + timedelta(minutes=2),
        "resend_request_ambiguous",
    )
    with factory() as session:
        record = session.scalar(select(OutboxRecord))
        assert record is not None and record.state == "dead_letter"


@pytest.mark.asyncio
async def test_active_final_attempt_lease_is_not_dead_lettered_by_competing_worker(persistence) -> None:
    factory, order_id = persistence
    await record_observation(factory, order_id, 1)
    outbox = SqlNotificationOutbox(factory, max_attempts=1)
    await outbox.enqueue(
        notification_message(
            NotificationKind.INITIAL_CONFIRMATION,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            1,
        )
    )
    claim_time = datetime.now(UTC)
    active = await outbox.claim("worker-a", claim_time, timedelta(seconds=30))
    assert active is not None and active.attempt == 1
    assert await outbox.claim("worker-b", claim_time + timedelta(seconds=1), timedelta(seconds=30)) is None
    with factory() as session:
        record = session.scalar(select(OutboxRecord))
        attempt = session.scalar(select(NotificationAttempt))
        assert record is not None and record.state == "leased" and record.lease_owner == "worker-a"
        assert attempt is not None and attempt.finished_at is None

    assert await outbox.claim("worker-b", claim_time + timedelta(seconds=31), timedelta(seconds=30)) is None
    with factory() as session:
        record = session.scalar(select(OutboxRecord))
        attempt = session.scalar(select(NotificationAttempt))
        assert record is not None and record.state == "dead_letter"
        assert attempt is not None and attempt.outcome == "dead_letter"


@pytest.mark.asyncio
async def test_enqueue_requires_current_authoritative_observation(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    final = notification_message(
        NotificationKind.FINAL_CONFIRMATION,
        ORDER_REFERENCE,
        "private@example.test",
        1,
        6,
    )
    with pytest.raises(ValueError, match="observation_missing"):
        await outbox.enqueue(final)
    await record_observation(factory, order_id, 1)
    with pytest.raises(ValueError, match="observation_missing"):
        await outbox.enqueue(final)
    await record_observation(factory, order_id, 6)
    await outbox.enqueue(final)
    await record_observation(factory, order_id, 7)
    await outbox.enqueue(
        notification_message(
            NotificationKind.FINAL_CONFIRMATION,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            7,
        )
    )
    with factory() as session:
        record = session.scalar(select(OutboxRecord))
        assert record is not None and record.confirmation_observation_id is not None
        assert record.confirmation_count == 6
        assert session.scalar(select(func.count()).select_from(OutboxRecord)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "confirmations", "expected_order_state"),
    [
        (NotificationKind.INITIAL_CONFIRMATION, 1, "delivered"),
        (NotificationKind.FINAL_CONFIRMATION, 6, "bitcoin_verified"),
    ],
)
async def test_pre_acceptance_delivered_webhook_is_reconciled_atomically(
    persistence,
    kind,
    confirmations,
    expected_order_state,
) -> None:
    factory, order_id = persistence
    await record_observation(factory, order_id, confirmations)
    outbox = SqlNotificationOutbox(factory)
    store = SqlResendWebhookStore(factory)
    await outbox.enqueue(
        notification_message(
            kind,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            confirmations,
        )
    )
    claim_time = datetime.now(UTC)
    claim = await outbox.claim("worker-a", claim_time, timedelta(minutes=1))
    assert claim is not None
    unmatched = await store.process(
        provider_event("msg-before-acceptance", "email.delivered", "provider-before-acceptance"),
        hashlib.sha256(b"before-acceptance").digest(),
        NOW,
    )
    assert not unmatched.notification_delivered

    await outbox.record_accepted(
        claim,
        ProviderAcceptance("provider-before-acceptance", 200),
        claim_time,
    )
    with factory() as session:
        order = session.get(Order, order_id)
        record = session.scalar(select(OutboxRecord))
        assert order is not None and order.fulfillment_state == expected_order_state
        assert record is not None and record.state == "delivered" and record.delivered_at is not None
        assert record.delivered_at.replace(tzinfo=UTC) == NOW
        assert session.scalar(select(func.count()).select_from(ResendWebhookEvent)) == 1


@pytest.mark.asyncio
async def test_final_delivery_rejects_newer_authoritative_observation_below_milestone(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    store = SqlResendWebhookStore(factory)
    await enqueue_and_accept(outbox, NotificationKind.FINAL_CONFIRMATION, "provider-final-reorg", 6)
    await record_observation(factory, order_id, 1, observed_at=NOW + timedelta(minutes=1))
    result = await store.process(
        provider_event("msg-final-reorg", "email.delivered", "provider-final-reorg"),
        hashlib.sha256(b"final-reorg").digest(),
        NOW,
    )
    assert not result.notification_delivered
    with factory() as session:
        assert session.get(Order, order_id).fulfillment_state == "bitcoin_verified"
        assert session.scalar(select(OutboxRecord)).state == "accepted"


@pytest.mark.asyncio
async def test_notification_dispatcher_claims_only_supported_milestone_rows(persistence) -> None:
    factory, order_id = persistence
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        session.add(
            OutboxRecord(
                message_key="payment-confirmed-test",
                order_id=order_id,
                kind="payment_confirmed",
                recipient="private@example.test",
                payload={"template": "payment_confirmed"},
                state="available",
                attempt_count=0,
                max_attempts=12,
                available_at=now,
                lease_owner=None,
                lease_until=None,
                lease_token=None,
                proof_version=None,
                confirmation_count=None,
                confirmation_observation_id=None,
                provider_message_id=None,
                accepted_at=None,
                delivered_at=None,
                idempotency_expires_at=now + timedelta(hours=24),
                safe_error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    outbox = SqlNotificationOutbox(factory)
    assert await outbox.claim("worker-a", now + timedelta(seconds=1), timedelta(seconds=30)) is None
    with factory() as session:
        record = session.scalar(select(OutboxRecord))
        assert record is not None and record.state == "available" and record.attempt_count == 0


@pytest.mark.asyncio
async def test_only_delivered_webhook_transitions_initial_order_and_replay_is_deduped(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    store = SqlResendWebhookStore(factory)
    await enqueue_and_accept(outbox, NotificationKind.INITIAL_CONFIRMATION, "provider-initial", 1)

    sent_payload_hash = hashlib.sha256(b"sent").digest()
    sent = await store.process(provider_event("msg-sent", "email.sent", "provider-initial"), sent_payload_hash, NOW)
    assert not sent.notification_delivered
    with factory() as session:
        assert session.get(Order, order_id).fulfillment_state == "bitcoin_verified"
        assert session.scalar(select(OutboxRecord)).state == "accepted"

    delivered_hash = hashlib.sha256(b"delivered").digest()
    delivered = await store.process(
        provider_event("msg-delivered", "email.delivered", "provider-initial"),
        delivered_hash,
        NOW,
    )
    assert delivered.notification_delivered and delivered.order_transitioned
    replay = await store.process(
        provider_event("msg-delivered", "email.delivered", "provider-initial"),
        delivered_hash,
        NOW,
    )
    assert replay.duplicate
    with factory() as session:
        assert session.get(Order, order_id).fulfillment_state == "delivered"
        assert session.scalar(select(OutboxRecord)).state == "delivered"
        assert session.scalar(select(func.count()).select_from(ResendWebhookEvent)) == 2
        event = session.scalar(select(StateEvent).where(StateEvent.source == "resend_webhook"))
        assert event is not None
        serialized = str(event.evidence).lower()
        for forbidden in ("private@", "certificate", "digest", "proof_bytes", "token"):
            assert forbidden not in serialized


@pytest.mark.asyncio
async def test_final_six_has_independent_provider_evidence_without_order_transition(persistence) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    store = SqlResendWebhookStore(factory)
    initial_claim = await enqueue_and_accept(
        outbox,
        NotificationKind.INITIAL_CONFIRMATION,
        "provider-initial",
        1,
    )
    final_claim = await enqueue_and_accept(
        outbox,
        NotificationKind.FINAL_CONFIRMATION,
        "provider-final",
        6,
    )
    assert initial_claim.idempotency_key != final_claim.idempotency_key
    result = await store.process(
        provider_event("msg-final", "email.delivered", "provider-final"),
        hashlib.sha256(b"final").digest(),
        NOW,
    )
    assert result.notification_delivered and not result.order_transitioned
    with factory() as session:
        assert session.get(Order, order_id).fulfillment_state == "bitcoin_verified"
        rows = session.scalars(select(OutboxRecord).order_by(OutboxRecord.kind)).all()
        states = {row.kind: (row.state, row.provider_message_id) for row in rows}
        assert states[NotificationKind.FINAL_CONFIRMATION.value] == ("delivered", "provider-final")
        assert states[NotificationKind.INITIAL_CONFIRMATION.value] == ("accepted", "provider-initial")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["email.bounced", "email.failed", "email.complained"])
async def test_negative_provider_events_neither_deliver_nor_resend(persistence, event_type) -> None:
    factory, order_id = persistence
    outbox = SqlNotificationOutbox(factory)
    store = SqlResendWebhookStore(factory)
    await enqueue_and_accept(outbox, NotificationKind.INITIAL_CONFIRMATION, "provider-negative", 1)
    result = await store.process(
        provider_event(f"msg-{event_type}", event_type, "provider-negative"),
        hashlib.sha256(event_type.encode()).digest(),
        NOW,
    )
    assert not result.notification_delivered
    assert await outbox.claim("worker-b", datetime.now(UTC), timedelta(seconds=30)) is None
    with factory() as session:
        assert session.get(Order, order_id).fulfillment_state == "bitcoin_verified"
        assert session.scalar(select(OutboxRecord)).state == "failed"
