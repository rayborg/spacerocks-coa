from __future__ import annotations

from dataclasses import dataclass

from app.domain.order import FulfillmentState, PaymentState
from app.fulfillment.errors import ConfirmationPending, ManualReviewError, RetryableFulfillmentError
from app.fulfillment.ports import (
    BundleRepository,
    FulfillmentOrder,
    FulfillmentRepository,
    VerificationRepository,
)
from app.ports.bitcoin import BitcoinVerification, BitcoinVerifier
from app.ports.notifications import Outbox, OutboxMessage
from app.ports.proof import ProofBundleContext, ProofBundler, ProofState, ProofStore
from app.ports.timestamping import Timestamper
from app.proofs.receipt import ReceiptInput, build_receipt
from app.proofs.store import make_stored_proof
from app.timestamping.detached import ProofValidationError, validate_exact_proof


async def _load(repository: FulfillmentRepository, order_id: str) -> FulfillmentOrder:
    order = await repository.get_for_fulfillment(order_id)
    if order is None:
        raise ManualReviewError("order_not_found")
    return order


@dataclass(slots=True)
class StampHandler:
    orders: FulfillmentRepository
    proofs: ProofStore
    timestamper: Timestamper

    async def __call__(self, order_id: str) -> None:
        order = await _load(self.orders, order_id)
        snapshot = order.state.snapshot
        latest = await self.proofs.latest(snapshot.order_reference)
        if latest is not None:
            try:
                validate_exact_proof(
                    snapshot.manifest_digest,
                    latest.proof_bytes,
                    expected_sha256=latest.proof_sha256,
                )
            except ProofValidationError as exc:
                raise ManualReviewError("stored_proof_invalid") from exc
            if latest.target_digest != snapshot.manifest_digest:
                raise ManualReviewError("stored_proof_target_mismatch")
            submission_time = latest.calendar_submitted_at
            if order.calendar_submitted_at is not None and order.calendar_submitted_at != submission_time:
                raise ManualReviewError("proof_submission_time_conflict")
            if order.state.fulfillment in {FulfillmentState.QUEUED, FulfillmentState.STAMPING}:
                if order.state.fulfillment == FulfillmentState.QUEUED:
                    order = await self.orders.transition_fulfillment_once(
                        order_id,
                        FulfillmentState.STAMPING,
                        f"stamp-replay-start-v{latest.version}",
                        calendar_submitted_at=submission_time,
                    )
                await self.orders.transition_fulfillment_once(
                    order_id,
                    FulfillmentState.CALENDAR_PENDING,
                    f"stamp-replay-v{latest.version}",
                    calendar_submitted_at=submission_time,
                )
            return

        if order.state.payment != PaymentState.PAID:
            # Refund, dispute, failure, and redirect state never authorize new external work.
            raise ManualReviewError("initial_stamp_not_paid")
        if order.state.fulfillment == FulfillmentState.QUEUED:
            order = await self.orders.transition_fulfillment_once(
                order_id,
                FulfillmentState.STAMPING,
                "stamp-start-v1",
            )
        if order.state.fulfillment != FulfillmentState.STAMPING:
            raise ManualReviewError("stamp_state_invalid")
        try:
            # Public calendars cannot atomically commit with our database. A crash before
            # proof append is recovered by at-least-once resubmission of this same digest.
            pending = await self.timestamper.stamp_exact_digest(snapshot.manifest_digest)
            validate_exact_proof(snapshot.manifest_digest, pending.proof_bytes)
        except ProofValidationError as exc:
            raise ManualReviewError("calendar_proof_invalid") from exc
        except Exception as exc:
            raise RetryableFulfillmentError("calendar_submit_unavailable") from exc

        stored = make_stored_proof(
            snapshot.order_reference,
            1,
            snapshot.manifest_digest,
            pending.proof_bytes,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=None,
        )
        try:
            await self.proofs.append(stored)
        except ValueError as exc:
            replay = await self.proofs.latest(snapshot.order_reference)
            if replay != stored:
                raise ManualReviewError("proof_append_conflict") from exc
        await self.orders.transition_fulfillment_once(
            order_id,
            FulfillmentState.STAMPING,
            "stamp-response-v1",
            calendar_submitted_at=pending.calendar_submitted_at,
        )
        await self.orders.transition_fulfillment_once(
            order_id,
            FulfillmentState.CALENDAR_PENDING,
            "stamp-persisted-v1",
            calendar_submitted_at=pending.calendar_submitted_at,
        )


@dataclass(slots=True)
class UpgradeHandler:
    orders: FulfillmentRepository
    proofs: ProofStore
    timestamper: Timestamper
    bitcoin: BitcoinVerifier
    verifications: VerificationRepository

    async def __call__(self, order_id: str) -> None:
        order = await _load(self.orders, order_id)
        snapshot = order.state.snapshot
        latest = await self.proofs.latest(snapshot.order_reference)
        if latest is None:
            raise ManualReviewError("proof_missing")
        try:
            validate_exact_proof(
                snapshot.manifest_digest,
                latest.proof_bytes,
                expected_sha256=latest.proof_sha256,
            )
        except ProofValidationError as exc:
            raise ManualReviewError("stored_proof_invalid") from exc
        if order.calendar_submitted_at is None or order.calendar_submitted_at != latest.calendar_submitted_at:
            raise ManualReviewError("proof_submission_time_conflict")
        if order.state.fulfillment in {FulfillmentState.BITCOIN_VERIFIED, FulfillmentState.DELIVERED}:
            verification = await self.bitcoin.verify_exact_digest(snapshot.manifest_digest, latest.proof_bytes)
            if not verification.verified:
                raise ManualReviewError("bitcoin_reverification_failed")
            if not _same_reverification_evidence(latest.verification, verification):
                raise ManualReviewError("bitcoin_reverification_metadata_conflict")
            assert verification.verified_at is not None
            await self.orders.transition_fulfillment_once(
                order_id,
                order.state.fulfillment,
                f"bitcoin-reverified-v{latest.version}-{int(verification.verified_at.timestamp() * 1_000_000)}",
            )
            return
        if order.state.fulfillment != FulfillmentState.CALENDAR_PENDING:
            raise ManualReviewError("upgrade_state_invalid")
        try:
            upgraded_bytes = await self.timestamper.upgrade_exact_digest(
                snapshot.manifest_digest,
                latest.proof_bytes,
            )
            validate_exact_proof(snapshot.manifest_digest, upgraded_bytes)
        except ProofValidationError as exc:
            raise ManualReviewError("upgraded_proof_invalid") from exc
        except Exception as exc:
            raise RetryableFulfillmentError("calendar_upgrade_unavailable") from exc
        if upgraded_bytes != latest.proof_bytes:
            upgraded = make_stored_proof(
                snapshot.order_reference,
                latest.version + 1,
                snapshot.manifest_digest,
                upgraded_bytes,
                proof_state=ProofState.CALENDAR_PENDING,
                calendar_submitted_at=latest.calendar_submitted_at,
                verification=None,
            )
            try:
                await self.proofs.append(upgraded)
            except ValueError as exc:
                replay = await self.proofs.latest(snapshot.order_reference)
                if replay != upgraded:
                    raise ManualReviewError("proof_append_conflict") from exc
            latest = upgraded
        verification = await self.bitcoin.verify_exact_digest(snapshot.manifest_digest, latest.proof_bytes)
        if not verification.verified:
            raise ConfirmationPending("bitcoin_confirmation_pending")
        await self.verifications.put_verified_once(order_id, latest.version, verification)
        await self.orders.transition_fulfillment_once(
            order_id,
            FulfillmentState.BITCOIN_VERIFIED,
            f"bitcoin-verified-v{latest.version}",
        )


@dataclass(slots=True)
class DeliveryHandler:
    orders: FulfillmentRepository
    proofs: ProofStore
    bundler: ProofBundler
    bundles: BundleRepository
    verifications: VerificationRepository
    outbox: Outbox
    service_version: str

    async def __call__(self, order_id: str) -> None:
        order = await _load(self.orders, order_id)
        if order.state.fulfillment != FulfillmentState.BITCOIN_VERIFIED:
            raise ManualReviewError("delivery_state_invalid")
        if order.calendar_submitted_at is None:
            raise ManualReviewError("proof_submission_time_missing")
        proof = await self.proofs.latest(order.state.snapshot.order_reference)
        if proof is None:
            raise ManualReviewError("proof_missing")
        if proof.calendar_submitted_at != order.calendar_submitted_at:
            raise ManualReviewError("proof_submission_time_conflict")
        verification = await self.verifications.get_verified(order_id, proof.version)
        if verification is None or not verification.verified:
            raise ManualReviewError("delivery_verification_metadata_missing")
        if proof.proof_state != ProofState.BITCOIN_VERIFIED or proof.verification != verification:
            raise ManualReviewError("delivery_proof_metadata_mismatch")
        receipt = build_receipt(
            ReceiptInput(
                proof=proof,
                certificate_reference=order.state.snapshot.certificate_reference,
                service_version=self.service_version,
            )
        )
        bundle = await self.bundler.build(
            proof,
            receipt,
            ProofBundleContext(
                certificate_reference=order.state.snapshot.certificate_reference,
                service_version=self.service_version,
            ),
        )
        await self.bundles.put_once(order_id, proof.version, bundle)
        await self.outbox.enqueue(completion_message(order, proof.version))


def completion_message(order: FulfillmentOrder, proof_version: int) -> OutboxMessage:
    snapshot = order.state.snapshot
    return OutboxMessage(
        message_key=f"timestamp-complete-v{proof_version}-{snapshot.order_reference.value}",
        order_reference=snapshot.order_reference,
        template="timestamp-complete",
        recipient=snapshot.email,
        variables={"order_reference": snapshot.order_reference.value},
    )


def _same_reverification_evidence(
    existing: BitcoinVerification | None,
    current: BitcoinVerification,
) -> bool:
    if (
        existing is None
        or not existing.verified
        or not current.verified
        or existing.verified_at is None
        or current.verified_at is None
    ):
        return False
    immutable_existing = (
        existing.method,
        existing.block_height,
        existing.block_hash,
        existing.block_time,
        existing.confirmation_policy,
    )
    immutable_current = (
        current.method,
        current.block_height,
        current.block_hash,
        current.block_time,
        current.confirmation_policy,
    )
    return immutable_current == immutable_existing and current.verified_at >= existing.verified_at
