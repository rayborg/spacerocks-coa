from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.bitcoin.disabled import DisabledVerifier
from app.bitcoin.fixture import FixtureBitcoinVerifier
from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.domain.order import FulfillmentState, OrderSnapshot, OrderState, PaymentState
from app.fulfillment.errors import ConfirmationPending, ManualReviewError, RetryableFulfillmentError
from app.fulfillment.handlers import ConfirmationHandler, StampHandler, UpgradeHandler
from app.fulfillment.ports import FulfillmentOrder
from app.ports.bitcoin import BitcoinEvidenceInvalid, BitcoinVerification, BitcoinVerifierUnavailable
from app.ports.notifications import ConfirmationObservation
from app.ports.proof import ProofState
from app.proofs.store import InMemoryProofStore, make_stored_proof
from app.timestamping.fixture import FixtureTimestamper


def _state(
    payment: PaymentState = PaymentState.PAID,
    fulfillment: FulfillmentState = FulfillmentState.QUEUED,
) -> OrderState:
    snapshot = OrderSnapshot(
        order_reference=OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        certificate_reference=CertificateReference("AZ-2019-0447-HE"),
        manifest_digest=ManifestDigest.from_hex("de" * 32),
        email="private@example.com",
        amount_minor=100,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        consent_terms_version="1",
        consent_privacy_version="1",
        consent_accepted_at=datetime(2026, 7, 30, tzinfo=UTC),
    )
    return OrderState(snapshot=snapshot, payment=payment, fulfillment=fulfillment)


class FakeOrders:
    def __init__(self, state: OrderState, calendar_submitted_at: datetime | None = None) -> None:
        self.order = FulfillmentOrder("order_opaque", state, calendar_submitted_at)
        self.events: list[str] = []

    async def get_for_fulfillment(self, order_id: str) -> FulfillmentOrder | None:
        assert order_id == self.order.id
        return self.order

    async def transition_fulfillment_once(
        self,
        order_id: str,
        target: FulfillmentState,
        event_key: str,
        *,
        calendar_submitted_at: datetime | None = None,
    ) -> FulfillmentOrder:
        assert order_id == self.order.id
        if event_key in self.events:
            return self.order
        self.events.append(event_key)
        state = self.order.state.transition_fulfillment(target)
        submitted = calendar_submitted_at or self.order.calendar_submitted_at
        self.order = replace(self.order, state=state, calendar_submitted_at=submitted)
        return self.order


class FakeVerifications:
    def __init__(self) -> None:
        self.values: dict[tuple[str, int], BitcoinVerification] = {}

    async def put_verified_once(self, order_id: str, proof_version: int, result: BitcoinVerification) -> None:
        self.values.setdefault((order_id, proof_version), result)

    async def get_verified(self, order_id: str, proof_version: int) -> BitcoinVerification | None:
        return self.values.get((order_id, proof_version))


class FakeObservations:
    def __init__(self) -> None:
        self.values: dict[tuple[str, int], ConfirmationObservation] = {}

    async def record_once(
        self,
        order_id: str,
        proof_version: int,
        event_key: str,
        verification: BitcoinVerification,
    ) -> ConfirmationObservation:
        assert verification.confirmations is not None
        assert verification.block_height is not None
        assert verification.block_hash is not None
        assert verification.confirmation_policy is not None
        assert verification.verified_at is not None
        value = ConfirmationObservation(
            id=event_key,
            order_id=order_id,
            proof_version=proof_version,
            confirmations=verification.confirmations,
            block_height=verification.block_height,
            block_hash=verification.block_hash,
            method=verification.method,
            confirmation_policy=verification.confirmation_policy,
            observed_at=verification.verified_at,
            event_key=event_key,
        )
        self.values[(order_id, proof_version)] = value
        return value

    async def latest(self, order_id: str, proof_version: int) -> ConfirmationObservation | None:
        return self.values.get((order_id, proof_version))


class FakeOutbox:
    def __init__(self) -> None:
        self.messages = []

    async def enqueue(self, message) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_stamp_replay_reuses_persisted_proof_after_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(fulfillment=FulfillmentState.STAMPING)
    submitted_at = datetime(2026, 7, 30, 12, 5, tzinfo=UTC)
    orders = FakeOrders(state, submitted_at)
    timestamper = FixtureTimestamper()
    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    proofs = InMemoryProofStore()
    await proofs.append(
        make_stored_proof(
            state.snapshot.order_reference,
            1,
            state.snapshot.manifest_digest,
            pending.proof_bytes,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=None,
        )
    )
    calls_before = timestamper.stamp_calls
    handler = StampHandler(orders, proofs, timestamper)
    await handler("order_opaque")
    await handler("order_opaque")
    assert timestamper.stamp_calls == calls_before
    assert orders.order.state.fulfillment == FulfillmentState.CALENDAR_PENDING
    assert len(await proofs.versions(state.snapshot.order_reference)) == 1


@pytest.mark.asyncio
async def test_refund_does_not_authorize_initial_stamp_or_delete_existing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(payment=PaymentState.REFUNDED)
    orders = FakeOrders(state)
    proofs = InMemoryProofStore()
    timestamper = FixtureTimestamper()
    with pytest.raises(ManualReviewError, match="initial_stamp_not_paid"):
        await StampHandler(orders, proofs, timestamper)("order_opaque")
    assert timestamper.stamp_calls == 0

    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    evidence = make_stored_proof(
        state.snapshot.order_reference,
        1,
        state.snapshot.manifest_digest,
        pending.proof_bytes,
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=pending.calendar_submitted_at,
        verification=None,
    )
    await proofs.append(evidence)
    assert await proofs.latest(state.snapshot.order_reference) == evidence


@pytest.mark.asyncio
async def test_pending_or_disabled_verification_cannot_transition_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(fulfillment=FulfillmentState.CALENDAR_PENDING)
    orders = FakeOrders(state, datetime(2026, 7, 30, 12, 5, tzinfo=UTC))
    proofs = InMemoryProofStore()
    timestamper = FixtureTimestamper()
    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    await proofs.append(
        make_stored_proof(
            state.snapshot.order_reference,
            1,
            state.snapshot.manifest_digest,
            pending.proof_bytes,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=None,
        )
    )
    handler = UpgradeHandler(
        orders,
        proofs,
        timestamper,
        DisabledVerifier(),
        FakeVerifications(),
        FakeObservations(),
    )
    with pytest.raises(ConfirmationPending, match="bitcoin_confirmation_pending"):
        await handler("order_opaque")
    assert orders.order.state.fulfillment == FulfillmentState.CALENDAR_PENDING


@pytest.mark.asyncio
async def test_pre_persistence_calendar_crash_repeats_only_the_same_exact_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state()
    orders = FakeOrders(state)
    proofs = InMemoryProofStore()

    class AcceptedThenCrashed:
        def __init__(self) -> None:
            self.fixture = FixtureTimestamper()
            self.targets: list[bytes] = []

        async def stamp_exact_digest(self, digest: ManifestDigest):
            self.targets.append(digest.ots_target())
            result = await self.fixture.stamp_exact_digest(digest)
            if len(self.targets) == 1:
                raise RuntimeError("process_died_after_calendar_acceptance")
            return result

        async def upgrade_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> bytes:
            return await self.fixture.upgrade_exact_digest(digest, proof_bytes)

    timestamper = AcceptedThenCrashed()
    handler = StampHandler(orders, proofs, timestamper)
    with pytest.raises(RetryableFulfillmentError, match="calendar_submit_unavailable"):
        await handler("order_opaque")
    assert await proofs.latest(state.snapshot.order_reference) is None
    await handler("order_opaque")
    assert timestamper.targets == [state.snapshot.manifest_digest.value] * 2
    stored = await proofs.latest(state.snapshot.order_reference)
    assert stored is not None and stored.target_digest == state.snapshot.manifest_digest


@pytest.mark.asyncio
async def test_successful_reverification_appends_idempotent_state_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(fulfillment=FulfillmentState.BITCOIN_VERIFIED)
    orders = FakeOrders(state, datetime(2026, 7, 30, 12, 5, tzinfo=UTC))
    timestamper = FixtureTimestamper(confirm_on_upgrade=True)
    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    upgraded = await timestamper.upgrade_exact_digest(state.snapshot.manifest_digest, pending.proof_bytes)
    fixture_verifier = FixtureBitcoinVerifier()
    verification = await fixture_verifier.verify_exact_digest(state.snapshot.manifest_digest, upgraded)
    proofs = InMemoryProofStore()
    await proofs.append(
        make_stored_proof(
            state.snapshot.order_reference,
            1,
            state.snapshot.manifest_digest,
            upgraded,
            proof_state=ProofState.BITCOIN_VERIFIED,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=verification,
        )
    )
    assert verification.verified_at is not None
    later_verification = replace(verification, verified_at=verification.verified_at + timedelta(hours=1))

    class RecheckVerifier:
        def __init__(self, result: BitcoinVerification) -> None:
            self.result = result

        async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
            del digest, proof_bytes
            return self.result

    handler = UpgradeHandler(
        orders,
        proofs,
        timestamper,
        RecheckVerifier(later_verification),
        FakeVerifications(),
        FakeObservations(),
    )
    await handler("order_opaque")
    await handler("order_opaque")
    expected_event = f"bitcoin-reverified-v1-{int(later_verification.verified_at.timestamp() * 1_000_000)}"
    assert orders.events == [expected_event]
    assert orders.order.state.fulfillment == FulfillmentState.BITCOIN_VERIFIED

    for altered in (
        replace(later_verification, block_hash="cd" * 32),
        replace(later_verification, confirmation_policy="altered-policy"),
    ):
        hostile_handler = UpgradeHandler(
            orders,
            proofs,
            timestamper,
            RecheckVerifier(altered),
            FakeVerifications(),
            FakeObservations(),
        )
        with pytest.raises(ManualReviewError, match="bitcoin_reverification_metadata_conflict"):
            await hostile_handler("order_opaque")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verifier_error", "expected_error", "safe_code"),
    (
        (BitcoinVerifierUnavailable("rpc_down"), RetryableFulfillmentError, "bitcoin_verifier_unavailable"),
        (BitcoinEvidenceInvalid("wrong_merkle"), ManualReviewError, "bitcoin_evidence_invalid"),
    ),
)
async def test_bitcoin_verifier_errors_are_classified_safely(
    monkeypatch: pytest.MonkeyPatch,
    verifier_error: Exception,
    expected_error: type[Exception],
    safe_code: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(fulfillment=FulfillmentState.CALENDAR_PENDING)
    submitted_at = datetime(2026, 7, 30, 12, 5, tzinfo=UTC)
    orders = FakeOrders(state, submitted_at)
    proofs = InMemoryProofStore()
    timestamper = FixtureTimestamper()
    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    await proofs.append(
        make_stored_proof(
            state.snapshot.order_reference,
            1,
            state.snapshot.manifest_digest,
            pending.proof_bytes,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=submitted_at,
            verification=None,
        )
    )

    class FailingVerifier:
        async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
            del digest, proof_bytes
            raise verifier_error

    handler = UpgradeHandler(
        orders,
        proofs,
        timestamper,
        FailingVerifier(),
        FakeVerifications(),
        FakeObservations(),
    )
    with pytest.raises(expected_error, match=safe_code):
        await handler("order_opaque")
    assert orders.order.state.fulfillment == FulfillmentState.CALENDAR_PENDING


@pytest.mark.asyncio
async def test_confirmation_decrease_before_final_requires_manual_review_without_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    state = _state(fulfillment=FulfillmentState.BITCOIN_VERIFIED)
    orders = FakeOrders(state, datetime(2026, 7, 30, 12, 5, tzinfo=UTC))
    timestamper = FixtureTimestamper(confirm_on_upgrade=True)
    pending = await timestamper.stamp_exact_digest(state.snapshot.manifest_digest)
    proof_bytes = await timestamper.upgrade_exact_digest(state.snapshot.manifest_digest, pending.proof_bytes)
    initial = await FixtureBitcoinVerifier().verify_exact_digest(state.snapshot.manifest_digest, proof_bytes)
    proofs = InMemoryProofStore()
    await proofs.append(
        make_stored_proof(
            state.snapshot.order_reference,
            1,
            state.snapshot.manifest_digest,
            proof_bytes,
            proof_state=ProofState.BITCOIN_VERIFIED,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=initial,
        )
    )
    verifications = FakeVerifications()
    await verifications.put_verified_once("order_opaque", 1, initial)
    observations = FakeObservations()
    await observations.record_once("order_opaque", 1, "initial-six", initial)
    assert initial.verified_at is not None
    decreased = replace(initial, confirmations=5, verified_at=initial.verified_at + timedelta(minutes=15))

    class DecreasedVerifier:
        async def verify_exact_digest(self, digest, supplied_proof):
            del digest, supplied_proof
            return decreased

    outbox = FakeOutbox()
    handler = ConfirmationHandler(
        orders,
        proofs,
        DecreasedVerifier(),
        verifications,
        observations,
        outbox,
    )
    with pytest.raises(ManualReviewError, match="confirmation_count_decreased"):
        await handler("order_opaque")
    assert not outbox.messages
