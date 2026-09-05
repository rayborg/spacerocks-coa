from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    BitcoinConfirmationObservation,
    Order,
    ProofBundle,
    ProofVerification,
    ProofVersion,
    StateEvent,
)
from app.db.models import (
    OutboxMessage as OutboxRecord,
)
from app.db.notification_adapters import SqlBitcoinConfirmationObservations, SqlNotificationOutbox
from app.db.repositories import SqlJobClaimStore
from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.domain.order import FulfillmentState, OrderSnapshot, OrderState, PaymentState
from app.fulfillment.ports import FulfillmentOrder
from app.ports.bitcoin import BitcoinVerification
from app.ports.notifications import OutboxMessage
from app.ports.proof import MAX_PROOF_BYTES, ProofState, StoredProof
from app.tasks.dispatch import TaskDispatchCoordinator

if TYPE_CHECKING:
    from app.fulfillment.ports import BundleRepository, FulfillmentRepository, VerificationRepository
    from app.ports.notifications import Outbox
    from app.ports.proof import ProofStore

_SAFE_TEMPLATE = re.compile(r"^[a-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class ProofMetadata:
    id: uuid.UUID
    version: int
    target_digest: bytes
    proof_sha256: bytes
    declared_length: int
    actual_length: int
    proof_state: str
    calendar_submitted_at: datetime


class SqlProofStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def append(self, proof: StoredProof) -> None:
        if not 1 <= proof.proof_byte_length <= MAX_PROOF_BYTES:
            raise ValueError("proof_size_invalid")
        with self.session_factory() as session, session.begin():
            order = session.scalar(
                select(Order).where(Order.order_reference == proof.order_reference.value).with_for_update()
            )
            if order is None:
                raise ValueError("proof_order_not_found")
            if order.manifest_digest != proof.target_digest.value:
                raise ValueError("proof_target_does_not_match_order")
            _merge_calendar_time(order, proof.calendar_submitted_at)
            latest = session.scalar(self.latest_query(order.id, for_update=True))
            if latest is not None and proof.version == latest.version:
                if _stored_proof(latest, order.order_reference, None) == proof:
                    return
                raise ValueError("proof_version_conflict")
            if latest is None and proof.version != 1:
                raise ValueError("first_proof_version_must_be_one")
            if latest is not None:
                if proof.version != latest.version + 1:
                    raise ValueError("proof_version_must_append")
                if latest.target_digest != proof.target_digest.value:
                    raise ValueError("proof_target_is_immutable")
            metadata = _verification_payload(proof.verification) if proof.verification else None
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=proof.version,
                    target_digest=proof.target_digest.value,
                    proof_bytes=proof.proof_bytes,
                    proof_sha256=proof.proof_sha256,
                    proof_byte_length=proof.proof_byte_length,
                    proof_state=proof.proof_state.value,
                    calendar_submitted_at=proof.calendar_submitted_at,
                    verification_metadata=metadata,
                    created_at=datetime.now(UTC),
                )
            )
            if proof.verification is not None:
                _insert_verification(session, order.id, proof.version, proof.verification)

    async def latest(self, order_reference: OrderReference) -> StoredProof | None:
        with self.session_factory() as session, session.begin():
            order = session.scalar(
                select(Order).where(Order.order_reference == order_reference.value).with_for_update()
            )
            if order is None:
                return None
            return self.latest_for_order(session, order)

    @classmethod
    def latest_for_order(cls, session: Session, order: Order) -> StoredProof | None:
        metadata = session.execute(cls.latest_metadata_query(order.id)).one_or_none()
        if metadata is None:
            return None
        selected = ProofMetadata(*metadata)
        cls._validate_metadata(selected)
        proof = session.get(ProofVersion, selected.id)
        if proof is None:
            raise ValueError("stored_proof_disappeared")
        if not cls.matches_metadata(proof, selected):
            raise ValueError("stored_proof_changed")
        verification = session.scalar(
            select(ProofVerification).where(
                ProofVerification.order_id == order.id,
                ProofVerification.proof_version == proof.version,
            )
        )
        observation = (
            session.scalar(
                select(BitcoinConfirmationObservation)
                .where(
                    BitcoinConfirmationObservation.order_id == order.id,
                    BitcoinConfirmationObservation.proof_version == proof.version,
                )
                .order_by(
                    BitcoinConfirmationObservation.observed_at.desc(),
                    BitcoinConfirmationObservation.created_at.desc(),
                    BitcoinConfirmationObservation.id.desc(),
                )
                .limit(1)
            )
            if verification is not None
            else None
        )
        project_verification = order.fulfillment_state in {
            FulfillmentState.BITCOIN_VERIFIED.value,
            FulfillmentState.DELIVERED.value,
        }
        return _stored_proof(
            proof,
            order.order_reference,
            verification,
            confirmation_count=observation.observed_confirmations if observation is not None else None,
            project_verification=project_verification,
        )

    @staticmethod
    def latest_metadata_query(order_id: uuid.UUID) -> Select[tuple[object, ...]]:
        return (
            select(
                ProofVersion.id,
                ProofVersion.version,
                ProofVersion.target_digest,
                ProofVersion.proof_sha256,
                ProofVersion.proof_byte_length,
                func.length(ProofVersion.proof_bytes),
                ProofVersion.proof_state,
                ProofVersion.calendar_submitted_at,
            )
            .where(ProofVersion.order_id == order_id)
            .order_by(ProofVersion.version.desc())
            .limit(1)
        )

    @classmethod
    def latest_metadata(cls, session: Session, order_id: uuid.UUID) -> ProofMetadata | None:
        value = session.execute(cls.latest_metadata_query(order_id)).one_or_none()
        if value is None:
            return None
        metadata = ProofMetadata(*value)
        cls._validate_metadata(metadata)
        return metadata

    @staticmethod
    def matches_metadata(proof: ProofVersion, metadata: ProofMetadata) -> bool:
        return (
            proof.id == metadata.id
            and proof.version == metadata.version
            and proof.target_digest == metadata.target_digest
            and proof.proof_sha256 == metadata.proof_sha256
            and proof.proof_byte_length == metadata.declared_length
            and len(proof.proof_bytes) == metadata.actual_length
            and proof.proof_state == metadata.proof_state
            and _aware(proof.calendar_submitted_at) == _aware(metadata.calendar_submitted_at)
        )

    @staticmethod
    def _validate_metadata(metadata: ProofMetadata) -> None:
        if (
            not 1 <= metadata.declared_length <= MAX_PROOF_BYTES
            or metadata.actual_length != metadata.declared_length
            or len(metadata.proof_sha256) != 32
            or len(metadata.target_digest) != 32
        ):
            raise ValueError("stored_proof_metadata_invalid")

    @staticmethod
    def latest_query(order_id: uuid.UUID, *, for_update: bool = False) -> Select[tuple[ProofVersion]]:
        statement = (
            select(ProofVersion)
            .where(ProofVersion.order_id == order_id)
            .order_by(ProofVersion.version.desc())
            .limit(1)
        )
        return statement.with_for_update() if for_update else statement


class SqlFulfillmentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def get_for_fulfillment(self, order_id: str) -> FulfillmentOrder | None:
        with self.session_factory() as session:
            order = session.get(Order, _uuid(order_id))
            return _fulfillment_order(order) if order is not None else None

    async def transition_fulfillment_once(
        self,
        order_id: str,
        target: FulfillmentState,
        event_key: str,
        *,
        calendar_submitted_at: datetime | None = None,
    ) -> FulfillmentOrder:
        if not event_key or len(event_key) > 160:
            raise ValueError("fulfillment_event_key_invalid")
        if calendar_submitted_at is not None:
            _require_aware(calendar_submitted_at, "calendar_submission_time_invalid")
        with self.session_factory() as session, session.begin():
            order = session.scalar(select(Order).where(Order.id == _uuid(order_id)).with_for_update())
            if order is None:
                raise ValueError("fulfillment_order_not_found")
            existing = session.scalar(
                select(StateEvent).where(StateEvent.order_id == order.id, StateEvent.event_key == event_key)
            )
            if existing is not None:
                if existing.fulfillment_state != target.value:
                    raise ValueError("fulfillment_event_key_conflict")
                _merge_calendar_time(order, calendar_submitted_at)
                return _fulfillment_order(order)
            previous_payment = order.payment_state
            previous_fulfillment = order.fulfillment_state
            current = _order_state(order)
            transitioned = current.transition_fulfillment(target)
            _merge_calendar_time(order, calendar_submitted_at)
            if target == FulfillmentState.CALENDAR_PENDING and order.calendar_submitted_at is None:
                raise ValueError("calendar_submission_time_required")
            order.fulfillment_state = transitioned.fulfillment.value
            order.updated_at = datetime.now(UTC)
            sequence = session.scalar(select(func.max(StateEvent.sequence)).where(StateEvent.order_id == order.id)) or 0
            evidence: dict[str, object] = {}
            if order.calendar_submitted_at is not None:
                evidence["calendar_submitted_at"] = _aware(order.calendar_submitted_at).isoformat()
            session.add(
                StateEvent(
                    order_id=order.id,
                    sequence=sequence + 1,
                    event_key=event_key,
                    source="fulfillment_worker",
                    previous_payment_state=previous_payment,
                    payment_state=order.payment_state,
                    previous_fulfillment_state=previous_fulfillment,
                    fulfillment_state=order.fulfillment_state,
                    evidence=evidence,
                    created_at=order.updated_at,
                )
            )
            return _fulfillment_order(order)

    @staticmethod
    def locked_order_query(order_id: str) -> Select[tuple[Order]]:
        return select(Order).where(Order.id == _uuid(order_id)).with_for_update()


class SqlVerificationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def put_verified_once(self, order_id: str, proof_version: int, result: BitcoinVerification) -> None:
        _validate_verified(result)
        with self.session_factory() as session, session.begin():
            order_uuid = _uuid(order_id)
            order = session.scalar(select(Order).where(Order.id == order_uuid).with_for_update())
            if order is None:
                raise ValueError("verification_order_not_found")
            proof = session.scalar(
                select(ProofVersion).where(
                    ProofVersion.order_id == order_uuid,
                    ProofVersion.version == proof_version,
                )
            )
            if proof is None:
                raise ValueError("verification_proof_not_found")
            if proof.target_digest != order.manifest_digest:
                raise ValueError("verification_proof_target_conflict")
            existing = session.scalar(
                select(ProofVerification).where(
                    ProofVerification.order_id == order_uuid,
                    ProofVerification.proof_version == proof_version,
                )
            )
            if existing is not None:
                if not _stored_verification_matches(existing, result):
                    raise ValueError("verification_conflict")
                return
            _insert_verification(session, order_uuid, proof_version, result)

    async def get_verified(self, order_id: str, proof_version: int) -> BitcoinVerification | None:
        with self.session_factory() as session:
            order_uuid = _uuid(order_id)
            value = session.scalar(
                select(ProofVerification).where(
                    ProofVerification.order_id == order_uuid,
                    ProofVerification.proof_version == proof_version,
                )
            )
            if value is None:
                return None
            observation = session.scalar(
                select(BitcoinConfirmationObservation)
                .where(
                    BitcoinConfirmationObservation.order_id == order_uuid,
                    BitcoinConfirmationObservation.proof_version == proof_version,
                )
                .order_by(
                    BitcoinConfirmationObservation.observed_at.desc(),
                    BitcoinConfirmationObservation.created_at.desc(),
                    BitcoinConfirmationObservation.id.desc(),
                )
                .limit(1)
            )
            if observation is None:
                raise ValueError("verification_confirmation_observation_missing")
            if not _observation_matches_verification(observation, value):
                raise ValueError("verification_confirmation_observation_mismatch")
            return _verification(value, observation.observed_confirmations)


class SqlBundleRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def put_once(self, order_id: str, proof_version: int, bundle: bytes) -> None:
        bundle = bytes(bundle)
        if not 1 <= len(bundle) <= 12 * 1024 * 1024:
            raise ValueError("bundle_size_invalid")
        checksum = hashlib.sha256(bundle).digest()
        with self.session_factory() as session, session.begin():
            order_uuid = _uuid(order_id)
            order = session.scalar(select(Order).where(Order.id == order_uuid).with_for_update())
            if order is None:
                raise ValueError("bundle_order_not_found")
            lowered_bundle = bundle.lower()
            if (
                order.email.lower().encode("utf-8") in lowered_bundle
                or b"status_token" in lowered_bundle
                or b"authorization: bearer" in lowered_bundle
            ):
                raise ValueError("bundle_contains_sensitive_data")
            proof = session.scalar(
                select(ProofVersion).where(
                    ProofVersion.order_id == order_uuid,
                    ProofVersion.version == proof_version,
                )
            )
            if proof is None:
                raise ValueError("bundle_proof_not_found")
            if proof.target_digest != order.manifest_digest:
                raise ValueError("bundle_proof_target_conflict")
            existing = session.scalar(
                select(ProofBundle).where(
                    ProofBundle.order_id == order_uuid,
                    ProofBundle.proof_version == proof_version,
                )
            )
            if existing is not None:
                if (
                    existing.bundle_byte_length != len(bundle)
                    or existing.bundle_sha256 != checksum
                    or existing.bundle_bytes != bundle
                ):
                    raise ValueError("bundle_conflict")
                return
            session.add(
                ProofBundle(
                    order_id=order_uuid,
                    proof_version=proof_version,
                    bundle_bytes=bundle,
                    bundle_sha256=checksum,
                    bundle_byte_length=len(bundle),
                    created_at=datetime.now(UTC),
                )
            )

    async def get(self, order_id: str, proof_version: int) -> bytes | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(ProofBundle).where(
                    ProofBundle.order_id == _uuid(order_id),
                    ProofBundle.proof_version == proof_version,
                )
            )
            if record is None:
                return None
            if (
                record.bundle_byte_length != len(record.bundle_bytes)
                or hashlib.sha256(record.bundle_bytes).digest() != record.bundle_sha256
            ):
                raise ValueError("stored_bundle_invalid")
            return record.bundle_bytes


class SqlOutbox:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def enqueue(self, message: OutboxMessage) -> None:
        if not 1 <= len(message.message_key) <= 160 or not _SAFE_TEMPLATE.fullmatch(message.template):
            raise ValueError("outbox_message_identity_invalid")
        safe_variables = dict(message.variables)
        expected_variables = {"order_reference": message.order_reference.value}
        if safe_variables != expected_variables:
            raise ValueError("outbox_variables_not_allowlisted")
        payload: dict[str, object] = {
            "template": message.template,
            "order_reference": message.order_reference.value,
        }
        serialized = json.dumps(payload, sort_keys=True)
        if "bearer" in serialized.lower() or "token" in serialized.lower() or "@" in serialized:
            raise ValueError("outbox_payload_contains_sensitive_data")
        with self.session_factory() as session, session.begin():
            order = session.scalar(
                select(Order).where(Order.order_reference == message.order_reference.value).with_for_update()
            )
            if order is None:
                raise ValueError("outbox_order_not_found")
            if message.recipient != order.email:
                raise ValueError("outbox_recipient_does_not_match_order")
            existing = session.scalar(
                select(OutboxRecord).where(OutboxRecord.message_key == message.message_key)
            )
            if existing is not None:
                if (
                    existing.order_id != order.id
                    or existing.recipient != message.recipient
                    or existing.payload != payload
                ):
                    raise ValueError("outbox_message_key_conflict")
                return
            now = datetime.now(UTC)
            session.add(
                OutboxRecord(
                    message_key=message.message_key,
                    order_id=order.id,
                    kind=message.template,
                    recipient=message.recipient,
                    payload=payload,
                    state="available",
                    attempt_count=0,
                    available_at=now,
                    lease_owner=None,
                    lease_until=None,
                    provider_message_id=None,
                    delivered_at=None,
                    safe_error_code=None,
                    created_at=now,
                )
            )


@dataclass(frozen=True, slots=True)
class SqlFulfillmentAdapters:
    orders: SqlFulfillmentRepository
    proofs: SqlProofStore
    verifications: SqlVerificationRepository
    bundles: SqlBundleRepository
    observations: SqlBitcoinConfirmationObservations
    outbox: SqlNotificationOutbox
    jobs: SqlJobClaimStore


def create_sql_fulfillment_adapters(
    session_factory: sessionmaker[Session],
    task_dispatch: TaskDispatchCoordinator | None = None,
) -> SqlFulfillmentAdapters:
    return SqlFulfillmentAdapters(
        orders=SqlFulfillmentRepository(session_factory),
        proofs=SqlProofStore(session_factory),
        verifications=SqlVerificationRepository(session_factory),
        bundles=SqlBundleRepository(session_factory),
        observations=SqlBitcoinConfirmationObservations(session_factory),
        outbox=SqlNotificationOutbox(session_factory),
        jobs=SqlJobClaimStore(session_factory, task_dispatch),
    )


if TYPE_CHECKING:

    def _assert_protocol_compatibility(value: SqlFulfillmentAdapters) -> None:
        orders: FulfillmentRepository = value.orders
        proofs: ProofStore = value.proofs
        verifications: VerificationRepository = value.verifications
        bundles: BundleRepository = value.bundles
        outbox: Outbox = value.outbox
        del orders, proofs, verifications, bundles, outbox


def _stored_proof(
    value: ProofVersion,
    order_reference: str,
    separate_verification: ProofVerification | None,
    *,
    confirmation_count: int | None = None,
    project_verification: bool = True,
) -> StoredProof:
    verification = (
        _verification(separate_verification, confirmation_count)
        if separate_verification and confirmation_count is not None and project_verification
        else None
    )
    if project_verification and verification is None and value.verification_metadata is not None:
        verification = _verification_from_payload(value.verification_metadata)
    state = ProofState.BITCOIN_VERIFIED if verification is not None else ProofState.CALENDAR_PENDING
    if value.proof_byte_length != len(value.proof_bytes):
        raise ValueError("stored_proof_length_invalid")
    return StoredProof(
        order_reference=OrderReference(order_reference),
        version=value.version,
        target_digest=ManifestDigest.from_bytes(value.target_digest),
        proof_bytes=value.proof_bytes,
        proof_sha256=value.proof_sha256,
        proof_state=state,
        calendar_submitted_at=_aware(value.calendar_submitted_at),
        verification=verification,
    )


def _fulfillment_order(value: Order) -> FulfillmentOrder:
    return FulfillmentOrder(
        id=str(value.id),
        state=_order_state(value),
        calendar_submitted_at=_aware(value.calendar_submitted_at) if value.calendar_submitted_at else None,
    )


def _order_state(value: Order) -> OrderState:
    snapshot = OrderSnapshot(
        order_reference=OrderReference(value.order_reference),
        certificate_reference=CertificateReference(value.certificate_reference),
        manifest_digest=ManifestDigest.from_bytes(value.manifest_digest),
        email=value.email,
        amount_minor=value.amount_minor,
        currency=value.currency,
        product_version=value.product_version,
        payment_mode=value.payment_mode,
        consent_terms_version=value.consent_terms_version,
        consent_privacy_version=value.consent_privacy_version,
        consent_accepted_at=_aware(value.consent_accepted_at),
    )
    return OrderState(
        snapshot=snapshot,
        payment=PaymentState(value.payment_state),
        fulfillment=FulfillmentState(value.fulfillment_state),
    )


def _merge_calendar_time(order: Order, supplied: datetime | None) -> None:
    if supplied is None:
        return
    supplied = _aware(supplied)
    if order.calendar_submitted_at is None:
        order.calendar_submitted_at = supplied
    elif _aware(order.calendar_submitted_at) != supplied:
        raise ValueError("calendar_submission_time_conflict")


def _insert_verification(
    session: Session,
    order_id: uuid.UUID,
    proof_version: int,
    result: BitcoinVerification,
) -> None:
    _validate_verified(result)
    session.add(
        ProofVerification(
            order_id=order_id,
            proof_version=proof_version,
            method=result.method,
            verified_at=result.verified_at,
            block_height=result.block_height,
            block_hash=result.block_hash,
            block_time=result.block_time,
            confirmation_policy=result.confirmation_policy,
            created_at=datetime.now(UTC),
        )
    )


def _verification(value: ProofVerification, confirmations: int) -> BitcoinVerification:
    return BitcoinVerification(
        verified=True,
        method=value.method,
        verified_at=_aware(value.verified_at),
        block_height=value.block_height,
        block_hash=value.block_hash,
        block_time=_aware(value.block_time),
        confirmation_policy=value.confirmation_policy,
        confirmations=confirmations,
    )


def _verification_payload(value: BitcoinVerification) -> dict[str, object]:
    _validate_verified(value)
    assert value.block_time is not None
    assert value.verified_at is not None
    return {
        "bitcoin": {
            "block_height": value.block_height,
            "block_hash": value.block_hash,
            "block_time": value.block_time.isoformat(),
            "confirmation_policy": value.confirmation_policy,
            "confirmations": value.confirmations,
        },
        "verification_method": value.method,
        "verified_at": value.verified_at.isoformat(),
    }


def _verification_from_payload(value: dict[str, object]) -> BitcoinVerification:
    bitcoin = value.get("bitcoin")
    if not isinstance(bitcoin, dict):
        raise ValueError("stored_verification_metadata_invalid")
    try:
        return BitcoinVerification(
            verified=True,
            method=str(value["verification_method"]),
            verified_at=datetime.fromisoformat(str(value["verified_at"]).replace("Z", "+00:00")),
            block_height=int(bitcoin["block_height"]),
            block_hash=str(bitcoin["block_hash"]),
            block_time=datetime.fromisoformat(str(bitcoin["block_time"]).replace("Z", "+00:00")),
            confirmation_policy=str(bitcoin["confirmation_policy"]),
            confirmations=int(bitcoin["confirmations"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("stored_verification_metadata_invalid") from error


def _validate_verified(value: BitcoinVerification) -> None:
    if not value.verified:
        raise ValueError("verification_result_must_be_verified")
    if value.verified_at is None or value.block_time is None:
        raise ValueError("verification_metadata_incomplete")
    _require_aware(value.verified_at, "verification_time_invalid")
    _require_aware(value.block_time, "verification_block_time_invalid")
    if value.block_height is None or value.block_height < 0:
        raise ValueError("verification_height_invalid")
    if value.block_hash is None or len(value.block_hash) != 64 or value.block_hash.lower() != value.block_hash:
        raise ValueError("verification_block_hash_invalid")
    try:
        bytes.fromhex(value.block_hash)
    except ValueError as error:
        raise ValueError("verification_block_hash_invalid") from error
    if not 1 <= len(value.method) <= 128 or not value.confirmation_policy or len(value.confirmation_policy) > 128:
        raise ValueError("verification_method_or_policy_invalid")
    if value.confirmations is None or value.confirmations < 1:
        raise ValueError("verification_confirmation_count_invalid")


def _stored_verification_matches(stored: ProofVerification, result: BitcoinVerification) -> bool:
    return bool(
        stored.method == result.method
        and stored.block_height == result.block_height
        and stored.block_hash == result.block_hash
        and _aware(stored.block_time) == _aware(result.block_time)  # type: ignore[arg-type]
        and stored.confirmation_policy == result.confirmation_policy
    )


def _observation_matches_verification(
    observation: BitcoinConfirmationObservation,
    verification: ProofVerification,
) -> bool:
    return bool(
        observation.block_height == verification.block_height
        and observation.block_hash == verification.block_hash
        and observation.method == verification.method
        and observation.confirmation_policy == verification.confirmation_policy
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError("invalid_order_id") from error


def _require_aware(value: datetime, error_code: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(error_code)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
