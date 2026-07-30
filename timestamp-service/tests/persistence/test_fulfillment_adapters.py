from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql

from app.db.base import Base
from app.db.fulfillment_adapters import (
    SqlFulfillmentRepository,
    SqlProofStore,
    create_sql_fulfillment_adapters,
)
from app.db.models import Order, ProofBundle, ProofVerification, ProofVersion, StateEvent
from app.db.models import OutboxMessage as OutboxRecord
from app.db.session import create_session_factory
from app.domain.digest import ManifestDigest
from app.domain.identifiers import OrderReference
from app.domain.order import FulfillmentState
from app.ports.bitcoin import BitcoinVerification
from app.ports.notifications import OutboxMessage
from app.ports.proof import ProofState, StoredProof

ORDER_REFERENCE = OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB")
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
CALENDAR_TIME = datetime(2026, 7, 30, 12, 5, tzinfo=UTC)


@pytest.fixture
def adapters():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    order = Order(
        order_reference=ORDER_REFERENCE.value,
        certificate_reference="AZ-2019-0447-HE",
        manifest_digest=b"d" * 32,
        email="private@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        payment_state="paid",
        fulfillment_state="queued",
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=NOW,
        checkout_session_id="cs_test_adapters",
        payment_intent_id="pi_test_adapters",
        fulfillment_key="stamp:adapter-test",
        calendar_submitted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    with factory() as session, session.begin():
        session.add(order)
    aggregate = create_sql_fulfillment_adapters(factory)
    yield aggregate, factory, order.id
    engine.dispose()


def pending_proof(version: int = 1, content: bytes = b"pending-proof") -> StoredProof:
    return StoredProof(
        order_reference=ORDER_REFERENCE,
        version=version,
        target_digest=ManifestDigest.from_bytes(b"d" * 32),
        proof_bytes=content,
        proof_sha256=hashlib.sha256(content).digest(),
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=CALENDAR_TIME,
        verification=None,
    )


def verified() -> BitcoinVerification:
    return BitcoinVerification(
        verified=True,
        method="fixture-exact-digest",
        verified_at=datetime(2026, 7, 30, 15, 10, tzinfo=UTC),
        block_height=900000,
        block_hash="ab" * 32,
        block_time=datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
        confirmation_policy="phase0-fixture-exact-target",
    )


async def transition_to_verified(aggregate, order_id) -> None:
    await aggregate.orders.transition_fulfillment_once(
        str(order_id), FulfillmentState.STAMPING, "projection-stamping"
    )
    await aggregate.orders.transition_fulfillment_once(
        str(order_id),
        FulfillmentState.CALENDAR_PENDING,
        "projection-calendar-pending",
        calendar_submitted_at=CALENDAR_TIME,
    )
    await aggregate.orders.transition_fulfillment_once(
        str(order_id), FulfillmentState.BITCOIN_VERIFIED, "projection-bitcoin-verified"
    )


def test_stored_proof_invariants_are_truthful() -> None:
    proof = pending_proof()
    assert proof.proof_byte_length == len(proof.proof_bytes)
    with pytest.raises(ValueError, match="checksum"):
        StoredProof(
            order_reference=ORDER_REFERENCE,
            version=1,
            target_digest=proof.target_digest,
            proof_bytes=b"different",
            proof_sha256=proof.proof_sha256,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=CALENDAR_TIME,
            verification=None,
        )
    with pytest.raises(ValueError, match="pending_proof"):
        StoredProof(
            order_reference=ORDER_REFERENCE,
            version=1,
            target_digest=proof.target_digest,
            proof_bytes=proof.proof_bytes,
            proof_sha256=proof.proof_sha256,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=CALENDAR_TIME,
            verification=verified(),
        )
    with pytest.raises(ValueError, match="verified_proof"):
        StoredProof(
            order_reference=ORDER_REFERENCE,
            version=1,
            target_digest=proof.target_digest,
            proof_bytes=proof.proof_bytes,
            proof_sha256=proof.proof_sha256,
            proof_state=ProofState.BITCOIN_VERIFIED,
            calendar_submitted_at=CALENDAR_TIME,
            verification=None,
        )


@pytest.mark.asyncio
async def test_sql_proof_store_round_trips_and_rejects_conflicts(adapters) -> None:
    aggregate, factory, _order_id = adapters
    proof = pending_proof()
    await aggregate.proofs.append(proof)
    await aggregate.proofs.append(proof)
    assert await aggregate.proofs.latest(ORDER_REFERENCE) == proof
    with pytest.raises(ValueError, match="version_conflict"):
        await aggregate.proofs.append(pending_proof(content=b"conflicting-proof"))
    with pytest.raises(ValueError, match="version_must_append"):
        await aggregate.proofs.append(pending_proof(version=3, content=b"future-proof"))
    wrong_target = StoredProof(
        order_reference=ORDER_REFERENCE,
        version=2,
        target_digest=ManifestDigest.from_bytes(b"x" * 32),
        proof_bytes=b"wrong-target",
        proof_sha256=hashlib.sha256(b"wrong-target").digest(),
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=CALENDAR_TIME,
        verification=None,
    )
    with pytest.raises(ValueError, match="target"):
        await aggregate.proofs.append(wrong_target)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProofVersion)) == 1
        order = session.scalar(select(Order))
        assert order is not None and order.calendar_submitted_at is not None


@pytest.mark.asyncio
async def test_verified_proof_round_trips_every_field(adapters) -> None:
    aggregate, factory, order_id = adapters
    base = pending_proof()
    value = StoredProof(
        order_reference=base.order_reference,
        version=base.version,
        target_digest=base.target_digest,
        proof_bytes=base.proof_bytes,
        proof_sha256=base.proof_sha256,
        proof_state=ProofState.BITCOIN_VERIFIED,
        calendar_submitted_at=base.calendar_submitted_at,
        verification=verified(),
    )
    await aggregate.proofs.append(value)
    suppressed = await aggregate.proofs.latest(ORDER_REFERENCE)
    assert suppressed is not None
    assert suppressed.proof_state == ProofState.CALENDAR_PENDING
    assert suppressed.verification is None
    await transition_to_verified(aggregate, order_id)
    assert await aggregate.proofs.latest(ORDER_REFERENCE) == value
    await aggregate.orders.transition_fulfillment_once(
        str(order_id), FulfillmentState.MANUAL_REVIEW, "projection-manual-review"
    )
    manual_review = await aggregate.proofs.latest(ORDER_REFERENCE)
    assert manual_review is not None
    assert manual_review.proof_state == ProofState.CALENDAR_PENDING
    assert manual_review.verification is None
    with factory() as session:
        row = session.scalar(select(ProofVersion))
        assert row is not None
        assert row.proof_byte_length == value.proof_byte_length
        assert row.proof_state == "bitcoin_verified"
        assert row.verification_metadata is not None
        assert session.scalar(select(func.count()).select_from(ProofVerification)) == 1


@pytest.mark.asyncio
async def test_verification_bundle_and_outbox_are_once_only_and_safe(adapters) -> None:
    aggregate, factory, order_id = adapters
    await aggregate.proofs.append(pending_proof())
    result = verified()
    await aggregate.verifications.put_verified_once(str(order_id), 1, result)
    await aggregate.verifications.put_verified_once(str(order_id), 1, result)
    assert await aggregate.verifications.get_verified(str(order_id), 1) == result
    await transition_to_verified(aggregate, order_id)
    latest = await aggregate.proofs.latest(ORDER_REFERENCE)
    assert latest is not None
    assert latest.proof_state == ProofState.BITCOIN_VERIFIED
    assert latest.verification == result
    conflict = BitcoinVerification(
        verified=True,
        method="different-method",
        verified_at=result.verified_at,
        block_height=result.block_height,
        block_hash=result.block_hash,
        block_time=result.block_time,
        confirmation_policy=result.confirmation_policy,
    )
    with pytest.raises(ValueError, match="verification_conflict"):
        await aggregate.verifications.put_verified_once(str(order_id), 1, conflict)

    bundle = b"PK\x03\x04durable-bundle"
    await aggregate.bundles.put_once(str(order_id), 1, bundle)
    await aggregate.bundles.put_once(str(order_id), 1, bundle)
    assert await aggregate.bundles.get(str(order_id), 1) == bundle
    with pytest.raises(ValueError, match="bundle_conflict"):
        await aggregate.bundles.put_once(str(order_id), 1, b"PK\x03\x04conflict")
    with pytest.raises(ValueError, match="sensitive"):
        await aggregate.bundles.put_once(str(order_id), 1, b"private@example.test")

    message = OutboxMessage(
        message_key="timestamp-complete-v1-order",
        order_reference=ORDER_REFERENCE,
        template="timestamp-complete",
        recipient="private@example.test",
        variables={"order_reference": ORDER_REFERENCE.value},
    )
    await aggregate.outbox.enqueue(message)
    await aggregate.outbox.enqueue(message)
    with pytest.raises(ValueError, match="not_allowlisted"):
        await aggregate.outbox.enqueue(
            OutboxMessage(
                message_key="unsafe",
                order_reference=ORDER_REFERENCE,
                template="timestamp-complete",
                recipient="private@example.test",
                variables={"order_reference": ORDER_REFERENCE.value, "status_token": "v1.secret"},
            )
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProofVerification)) == 1
        assert session.scalar(select(func.count()).select_from(ProofBundle)) == 1
        record = session.scalar(select(OutboxRecord))
        assert record is not None
        assert record.recipient == "private@example.test"
        serialized = json.dumps(record.payload)
        assert "@" not in serialized and "token" not in serialized.lower() and "bearer" not in serialized.lower()


@pytest.mark.asyncio
async def test_fulfillment_snapshot_transition_and_calendar_crash_replay(adapters) -> None:
    aggregate, factory, order_id = adapters
    loaded = await aggregate.orders.get_for_fulfillment(str(order_id))
    assert loaded is not None
    assert loaded.state.snapshot.email == "private@example.test"
    assert loaded.state.snapshot.manifest_digest.value == b"d" * 32
    stamping = await aggregate.orders.transition_fulfillment_once(
        str(order_id), FulfillmentState.STAMPING, "stamp-start-v1"
    )
    assert stamping.state.fulfillment == FulfillmentState.STAMPING
    response = await aggregate.orders.transition_fulfillment_once(
        str(order_id),
        FulfillmentState.STAMPING,
        "stamp-response-v1",
        calendar_submitted_at=CALENDAR_TIME,
    )
    assert response.calendar_submitted_at == CALENDAR_TIME
    replay = await aggregate.orders.transition_fulfillment_once(
        str(order_id),
        FulfillmentState.STAMPING,
        "stamp-response-v1",
        calendar_submitted_at=CALENDAR_TIME,
    )
    assert replay == response
    pending = await aggregate.orders.transition_fulfillment_once(
        str(order_id),
        FulfillmentState.CALENDAR_PENDING,
        "stamp-persisted-v1",
        calendar_submitted_at=CALENDAR_TIME,
    )
    assert pending.state.fulfillment == FulfillmentState.CALENDAR_PENDING
    assert pending.calendar_submitted_at == CALENDAR_TIME
    with pytest.raises(ValueError, match="time_conflict"):
        await aggregate.orders.transition_fulfillment_once(
            str(order_id),
            FulfillmentState.CALENDAR_PENDING,
            "conflicting-time",
            calendar_submitted_at=datetime(2026, 7, 30, 12, 6, tzinfo=UTC),
        )
    with factory() as session:
        events = session.scalars(select(StateEvent).order_by(StateEvent.sequence)).all()
        assert [event.event_key for event in events] == [
            "stamp-start-v1",
            "stamp-response-v1",
            "stamp-persisted-v1",
        ]


def test_postgres_adapters_compile_row_locks() -> None:
    order_id = "00000000-0000-0000-0000-000000000001"
    order_sql = str(
        SqlFulfillmentRepository.locked_order_query(order_id).compile(dialect=postgresql.dialect())
    )
    proof_sql = str(
        SqlProofStore.latest_query(
            __import__("uuid").UUID(order_id),
            for_update=True,
        ).compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in order_sql
    assert "FOR UPDATE" in proof_sql


@pytest.mark.asyncio
async def test_latest_rejects_oversized_metadata_before_loading_proof_blob() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    order = Order(
        order_reference=ORDER_REFERENCE.value,
        certificate_reference="AZ-2019-0447-HE",
        manifest_digest=b"d" * 32,
        email="private@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        payment_state="paid",
        fulfillment_state="calendar_pending",
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=NOW,
        fulfillment_key="stamp:oversized-test",
        calendar_submitted_at=CALENDAR_TIME,
        created_at=NOW,
        updated_at=NOW,
    )
    oversized = b"x" * 262_145
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
    with factory() as session, session.begin():
        session.add(order)
        session.flush()
        session.add(
            ProofVersion(
                order_id=order.id,
                version=1,
                target_digest=order.manifest_digest,
                proof_bytes=oversized,
                proof_sha256=hashlib.sha256(oversized).digest(),
                proof_byte_length=len(oversized),
                proof_state="calendar_pending",
                calendar_submitted_at=CALENDAR_TIME,
                verification_metadata=None,
                created_at=NOW,
            )
        )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
    statements: list[str] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        with pytest.raises(ValueError, match="stored_proof_metadata_invalid"):
            await SqlProofStore(factory).latest(ORDER_REFERENCE)
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
    proof_queries = [statement for statement in statements if "from proof_versions" in statement]
    assert len(proof_queries) == 1
    assert "length(proof_versions.proof_bytes)" in proof_queries[0]
    assert "proof_versions.proof_bytes as" not in proof_queries[0]
