from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

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
from app.domain.identifiers import OrderReference
from app.ports.bitcoin import BitcoinVerification
from app.ports.notifications import (
    ClaimedNotification,
    ConfirmationObservation,
    NotificationKind,
    OutboxMessage,
    ProviderAcceptance,
    VerifiedResendEvent,
    WebhookProcessResult,
    notification_message,
)

_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_EVENT_KEY = re.compile(r"^[A-Za-z0-9:_-]{1,160}$")
_SUPPORTED_KINDS = tuple(kind.value for kind in NotificationKind)


class SqlBitcoinConfirmationObservations:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def record_once(
        self,
        order_id: str,
        proof_version: int,
        event_key: str,
        verification: BitcoinVerification,
    ) -> ConfirmationObservation:
        _validate_observation_input(proof_version, event_key, verification)
        order_uuid = _uuid(order_id)
        assert verification.confirmations is not None
        assert verification.block_height is not None
        assert verification.block_hash is not None
        assert verification.verified_at is not None
        assert verification.confirmation_policy is not None
        with self.session_factory() as session, session.begin():
            order = session.scalar(select(Order).where(Order.id == order_uuid).with_for_update())
            if order is None:
                raise ValueError("confirmation_observation_order_not_found")
            proof = session.scalar(
                select(ProofVersion)
                .where(ProofVersion.order_id == order.id)
                .order_by(ProofVersion.version.desc())
                .with_for_update()
                .limit(1)
            )
            if proof is None or proof.version != proof_version or proof.target_digest != order.manifest_digest:
                raise ValueError("confirmation_observation_proof_not_current")
            stored_verification = session.scalar(
                select(ProofVerification).where(
                    ProofVerification.order_id == order.id,
                    ProofVerification.proof_version == proof_version,
                )
            )
            if stored_verification is None or not _verification_matches_result(stored_verification, verification):
                raise ValueError("confirmation_observation_verification_mismatch")
            existing = session.scalar(
                select(BitcoinConfirmationObservation).where(
                    BitcoinConfirmationObservation.order_id == order.id,
                    BitcoinConfirmationObservation.event_key == event_key,
                )
            )
            if existing is not None:
                if not _observation_matches_result(existing, proof_version, verification):
                    raise ValueError("confirmation_observation_event_conflict")
                return _project_observation(existing)
            now = datetime.now(UTC)
            observation = BitcoinConfirmationObservation(
                order_id=order.id,
                proof_version=proof_version,
                observed_confirmations=verification.confirmations,
                block_height=verification.block_height,
                block_hash=verification.block_hash,
                method=verification.method,
                confirmation_policy=verification.confirmation_policy,
                observed_at=_aware(verification.verified_at),
                event_key=event_key,
                created_at=now,
            )
            session.add(observation)
            session.flush([observation])
            return _project_observation(observation)

    async def latest(self, order_id: str, proof_version: int) -> ConfirmationObservation | None:
        with self.session_factory() as session:
            observation = session.scalar(_latest_observation_query(_uuid(order_id), proof_version))
            return _project_observation(observation) if observation is not None else None


class SqlNotificationOutbox:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        idempotency_horizon: timedelta = timedelta(hours=24),
        max_attempts: int = 12,
    ) -> None:
        if not timedelta(minutes=1) <= idempotency_horizon <= timedelta(hours=24):
            raise ValueError("notification_idempotency_horizon_invalid")
        if not 1 <= max_attempts <= 100:
            raise ValueError("notification_max_attempts_invalid")
        self.session_factory = session_factory
        self.idempotency_horizon = idempotency_horizon
        self.max_attempts = max_attempts

    async def enqueue(self, message: OutboxMessage) -> None:
        kind, proof_version, confirmation_count = _validate_message(message)
        payload: dict[str, object] = {
            "template": kind.value,
            "order_reference": message.order_reference.value,
        }
        serialized = json.dumps(payload, sort_keys=True)
        forbidden = ("bearer", "token", "digest", "certificate", "proof_bytes", "@")
        if any(value in serialized.lower() for value in forbidden):
            raise ValueError("outbox_payload_contains_sensitive_data")

        with self.session_factory() as session, session.begin():
            order = session.scalar(
                select(Order).where(Order.order_reference == message.order_reference.value).with_for_update()
            )
            if order is None:
                raise ValueError("outbox_order_not_found")
            if message.recipient != order.email:
                raise ValueError("outbox_recipient_does_not_match_order")
            observation = _authoritative_observation_for_enqueue(
                session,
                order,
                proof_version,
                confirmation_count,
            )
            existing = session.scalar(
                select(OutboxRecord).where(OutboxRecord.message_key == message.message_key)
            )
            if existing is not None:
                if not _same_message(
                    session,
                    existing,
                    order,
                    message,
                    payload,
                    proof_version,
                    observation,
                ):
                    raise ValueError("outbox_message_key_conflict")
                return
            now = datetime.now(UTC)
            session.add(
                OutboxRecord(
                    message_key=message.message_key,
                    order_id=order.id,
                    kind=kind.value,
                    recipient=message.recipient,
                    payload=payload,
                    state="available",
                    attempt_count=0,
                    max_attempts=self.max_attempts,
                    available_at=now,
                    lease_owner=None,
                    lease_until=None,
                    lease_token=None,
                    proof_version=proof_version,
                    confirmation_count=confirmation_count,
                    confirmation_observation_id=observation.id,
                    provider_message_id=None,
                    accepted_at=None,
                    delivered_at=None,
                    idempotency_expires_at=now + self.idempotency_horizon,
                    safe_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    async def claim(
        self,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ClaimedNotification | None:
        _validate_worker_and_time(worker_id, now, lease_for)
        now = _aware(now)
        with self.session_factory() as session, session.begin():
            self._expire_unclaimable(session, now)
            claimable = or_(
                OutboxRecord.state.in_(("available", "retry")),
                (OutboxRecord.state == "leased") & (OutboxRecord.lease_until < now),
            )
            record = session.scalar(
                select(OutboxRecord)
                .where(
                    claimable,
                    OutboxRecord.available_at <= now,
                    OutboxRecord.provider_message_id.is_(None),
                    OutboxRecord.kind.in_(_SUPPORTED_KINDS),
                    OutboxRecord.confirmation_observation_id.is_not(None),
                    OutboxRecord.idempotency_expires_at > now,
                    OutboxRecord.attempt_count < OutboxRecord.max_attempts,
                )
                .order_by(OutboxRecord.available_at, OutboxRecord.created_at, OutboxRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            previous_attempt = session.scalar(
                select(NotificationAttempt).where(
                    NotificationAttempt.outbox_id == record.id,
                    NotificationAttempt.finished_at.is_(None),
                )
            )
            lease_token = str(uuid.uuid4())
            lease_until = now + lease_for
            expected_state = record.state
            expected_token = record.lease_token
            next_attempt = record.attempt_count + 1
            claimed = session.execute(
                update(OutboxRecord)
                .where(
                    OutboxRecord.id == record.id,
                    OutboxRecord.state == expected_state,
                    OutboxRecord.lease_token.is_(None)
                    if expected_token is None
                    else OutboxRecord.lease_token == expected_token,
                    OutboxRecord.provider_message_id.is_(None),
                )
                .values(
                    state="leased",
                    attempt_count=next_attempt,
                    lease_owner=worker_id,
                    lease_until=lease_until,
                    lease_token=lease_token,
                    safe_error_code=None,
                    updated_at=now,
                )
            )
            if claimed.rowcount != 1:
                return None
            if previous_attempt is not None:
                previous_attempt.finished_at = now
                previous_attempt.outcome = "lease_expired"
                previous_attempt.safe_error_code = "lease_expired"
            attempt = next_attempt
            session.add(
                NotificationAttempt(
                    outbox_id=record.id,
                    attempt_number=attempt,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    started_at=now,
                    finished_at=None,
                    outcome=None,
                    response_status=None,
                    provider_message_id=None,
                    safe_error_code=None,
                    created_at=now,
                )
            )
            return _claimed(record, attempt, worker_id, lease_token, lease_until)

    async def record_accepted(
        self,
        message: ClaimedNotification,
        acceptance: ProviderAcceptance,
        now: datetime,
    ) -> None:
        if acceptance.response_status != 200 or not _SAFE_PROVIDER_ID.fullmatch(acceptance.message_id):
            raise ValueError("notification_provider_acceptance_invalid")
        with self.session_factory() as session, session.begin():
            record, attempt = _active_lease(session, message, now)
            record.state = "accepted"
            record.provider_message_id = acceptance.message_id
            record.accepted_at = _aware(now)
            record.lease_owner = None
            record.lease_until = None
            record.lease_token = None
            record.safe_error_code = None
            record.updated_at = _aware(now)
            _finish_attempt(
                attempt,
                now,
                "accepted",
                response_status=acceptance.response_status,
                provider_message_id=acceptance.message_id,
            )
            session.flush([record, attempt])
            delivered_event = session.scalar(
                select(ResendWebhookEvent)
                .where(
                    ResendWebhookEvent.provider_message_id == acceptance.message_id,
                    ResendWebhookEvent.event_type == "email.delivered",
                )
                .order_by(ResendWebhookEvent.event_created_at, ResendWebhookEvent.created_at)
                .limit(1)
            )
            if delivered_event is not None:
                _apply_delivered_event(session, record, delivered_event, _aware(now))

    async def record_retry(
        self,
        message: ClaimedNotification,
        now: datetime,
        retry_at: datetime,
        safe_error_code: str,
        response_status: int | None = None,
    ) -> None:
        _validate_safe_error(safe_error_code)
        if response_status is not None and not 100 <= response_status <= 599:
            raise ValueError("notification_response_status_invalid")
        now, retry_at = _aware(now), _aware(retry_at)
        if retry_at <= now:
            raise ValueError("notification_retry_time_invalid")
        with self.session_factory() as session, session.begin():
            record, attempt = _active_lease(session, message, now)
            exhausted = (
                record.idempotency_expires_at is None
                or retry_at >= _aware(record.idempotency_expires_at)
                or record.attempt_count >= record.max_attempts
            )
            record.state = "dead_letter" if exhausted else "retry"
            record.available_at = retry_at
            record.lease_owner = None
            record.lease_until = None
            record.lease_token = None
            record.safe_error_code = safe_error_code
            record.updated_at = now
            _finish_attempt(
                attempt,
                now,
                "dead_letter" if exhausted else "retry",
                response_status=response_status,
                safe_error_code=safe_error_code,
            )

    async def record_terminal_failure(
        self,
        message: ClaimedNotification,
        now: datetime,
        safe_error_code: str,
        response_status: int | None = None,
    ) -> None:
        _validate_safe_error(safe_error_code)
        if response_status is not None and not 100 <= response_status <= 599:
            raise ValueError("notification_response_status_invalid")
        with self.session_factory() as session, session.begin():
            record, attempt = _active_lease(session, message, now)
            record.state = "failed"
            record.lease_owner = None
            record.lease_until = None
            record.lease_token = None
            record.safe_error_code = safe_error_code
            record.updated_at = _aware(now)
            _finish_attempt(
                attempt,
                now,
                "failed",
                response_status=response_status,
                safe_error_code=safe_error_code,
            )

    @staticmethod
    def _expire_unclaimable(session: Session, now: datetime) -> None:
        exhausted = or_(
            OutboxRecord.idempotency_expires_at.is_(None),
            OutboxRecord.idempotency_expires_at <= now,
            OutboxRecord.attempt_count >= OutboxRecord.max_attempts,
        )
        terminal_candidate = or_(
            OutboxRecord.state.in_(("available", "retry")) & exhausted,
            (OutboxRecord.state == "leased") & (OutboxRecord.lease_until < now) & exhausted,
        )
        expired = session.scalars(
            select(OutboxRecord)
            .where(
                OutboxRecord.kind.in_(_SUPPORTED_KINDS),
                terminal_candidate,
                OutboxRecord.provider_message_id.is_(None),
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for record in expired:
            if record.state == "leased":
                attempt = session.scalar(
                    select(NotificationAttempt).where(
                        NotificationAttempt.outbox_id == record.id,
                        NotificationAttempt.finished_at.is_(None),
                    )
                )
                if attempt is not None:
                    _finish_attempt(
                        attempt,
                        now,
                        "dead_letter",
                        safe_error_code="idempotency_horizon_exhausted",
                    )
            record.state = "dead_letter"
            record.lease_owner = None
            record.lease_until = None
            record.lease_token = None
            record.safe_error_code = "idempotency_horizon_exhausted"
            record.updated_at = now


class SqlResendWebhookStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def process(
        self,
        event: VerifiedResendEvent,
        payload_sha256: bytes,
        now: datetime,
    ) -> WebhookProcessResult:
        if len(payload_sha256) != 32:
            raise ValueError("resend_webhook_payload_hash_invalid")
        now = _aware(now)
        with self.session_factory() as session, session.begin():
            existing = session.scalar(
                select(ResendWebhookEvent).where(ResendWebhookEvent.svix_event_id == event.svix_event_id)
            )
            if existing is not None:
                _validate_replay(existing, event, payload_sha256)
                return _reconcile_replayed_event(session, existing, now)

            record = session.scalar(
                select(OutboxRecord)
                .where(OutboxRecord.provider_message_id == event.provider_message_id)
                .with_for_update()
            )
            existing = session.scalar(
                select(ResendWebhookEvent).where(ResendWebhookEvent.svix_event_id == event.svix_event_id)
            )
            if existing is not None:
                _validate_replay(existing, event, payload_sha256)
                return _reconcile_replayed_event(session, existing, now)

            inserted = _insert_webhook_event(session, event, payload_sha256, now)
            if not inserted:
                existing = session.scalar(
                    select(ResendWebhookEvent).where(ResendWebhookEvent.svix_event_id == event.svix_event_id)
                )
                if existing is None:
                    raise RuntimeError("resend_webhook_dedupe_failed")
                _validate_replay(existing, event, payload_sha256)
                return _reconcile_replayed_event(session, existing, now)
            if record is None:
                return WebhookProcessResult(duplicate=False)
            if record.accepted_at is None or record.provider_message_id != event.provider_message_id:
                return WebhookProcessResult(duplicate=False)

            if event.event_type in {"email.bounced", "email.failed", "email.complained"}:
                if record.state != "delivered":
                    record.state = "failed"
                    record.safe_error_code = event.event_type.replace("email.", "email_")
                    record.updated_at = now
                return WebhookProcessResult(duplicate=False)
            if event.event_type != "email.delivered":
                return WebhookProcessResult(duplicate=False)
            stored_event = session.scalar(
                select(ResendWebhookEvent).where(ResendWebhookEvent.svix_event_id == event.svix_event_id)
            )
            if stored_event is None:
                raise RuntimeError("resend_webhook_event_missing")
            result = _apply_delivered_event(session, record, stored_event, now)
            return WebhookProcessResult(
                duplicate=False,
                notification_delivered=result.notification_delivered,
                order_transitioned=result.order_transitioned,
            )


def _reconcile_replayed_event(
    session: Session,
    event: ResendWebhookEvent,
    now: datetime,
) -> WebhookProcessResult:
    if event.event_type != "email.delivered":
        return WebhookProcessResult(duplicate=True)
    record = session.scalar(
        select(OutboxRecord)
        .where(OutboxRecord.provider_message_id == event.provider_message_id)
        .with_for_update()
    )
    if record is None or record.accepted_at is None:
        return WebhookProcessResult(duplicate=True)
    result = _apply_delivered_event(session, record, event, now)
    return WebhookProcessResult(
        duplicate=True,
        notification_delivered=result.notification_delivered,
        order_transitioned=result.order_transitioned,
    )


def _apply_delivered_event(
    session: Session,
    record: OutboxRecord,
    event: ResendWebhookEvent,
    now: datetime,
) -> WebhookProcessResult:
    if (
        record.state == "delivered"
        or record.accepted_at is None
        or record.provider_message_id != event.provider_message_id
        or event.event_type != "email.delivered"
    ):
        return WebhookProcessResult(duplicate=False)
    order = session.scalar(select(Order).where(Order.id == record.order_id).with_for_update())
    if order is None or not _delivery_evidence_matches(session, order, record):
        return WebhookProcessResult(duplicate=False)
    try:
        kind = NotificationKind(record.kind)
    except ValueError:
        return WebhookProcessResult(duplicate=False)
    if kind == NotificationKind.INITIAL_CONFIRMATION and order.fulfillment_state not in {
        "bitcoin_verified",
        "delivered",
    }:
        return WebhookProcessResult(duplicate=False)
    if kind == NotificationKind.FINAL_CONFIRMATION and order.fulfillment_state not in {
        "bitcoin_verified",
        "delivered",
    }:
        return WebhookProcessResult(duplicate=False)

    record.state = "delivered"
    record.delivered_at = _aware(event.event_created_at)
    record.safe_error_code = None
    record.updated_at = now
    session.flush([record])
    if kind == NotificationKind.FINAL_CONFIRMATION or order.fulfillment_state == "delivered":
        return WebhookProcessResult(duplicate=False, notification_delivered=True)

    previous_fulfillment = order.fulfillment_state
    order.fulfillment_state = "delivered"
    order.updated_at = now
    sequence = session.scalar(select(func.max(StateEvent.sequence)).where(StateEvent.order_id == order.id)) or 0
    session.add(
        StateEvent(
            order_id=order.id,
            sequence=sequence + 1,
            event_key=f"resend-delivered:{event.svix_event_id}",
            source="resend_webhook",
            previous_payment_state=order.payment_state,
            payment_state=order.payment_state,
            previous_fulfillment_state=previous_fulfillment,
            fulfillment_state=order.fulfillment_state,
            evidence={
                "notification_kind": kind.value,
                "proof_version": record.proof_version,
                "confirmation_count": record.confirmation_count,
            },
            created_at=now,
        )
    )
    return WebhookProcessResult(
        duplicate=False,
        notification_delivered=True,
        order_transitioned=True,
    )


def _insert_webhook_event(
    session: Session,
    event: VerifiedResendEvent,
    payload_sha256: bytes,
    now: datetime,
) -> bool:
    values = {
        "id": uuid.uuid4(),
        "svix_event_id": event.svix_event_id,
        "event_type": event.event_type,
        "provider_message_id": event.provider_message_id,
        "payload_sha256": payload_sha256,
        "event_created_at": _aware(event.event_created_at),
        "processed_at": now,
        "created_at": now,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return session.execute(
            postgres_insert(ResendWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["svix_event_id"])
        ).rowcount == 1
    if dialect == "sqlite":
        return session.execute(
            sqlite_insert(ResendWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["svix_event_id"])
        ).rowcount == 1
    session.add(ResendWebhookEvent(**values))
    session.flush()
    return True


def _validate_replay(
    existing: ResendWebhookEvent,
    event: VerifiedResendEvent,
    payload_sha256: bytes,
) -> None:
    if (
        existing.event_type != event.event_type
        or existing.provider_message_id != event.provider_message_id
        or existing.payload_sha256 != payload_sha256
        or _aware(existing.event_created_at) != _aware(event.event_created_at)
    ):
        raise ValueError("resend_webhook_replay_conflict")


def _delivery_evidence_matches(session: Session, order: Order, record: OutboxRecord) -> bool:
    if (
        record.state not in {"accepted", "failed"}
        or record.proof_version is None
        or record.confirmation_count is None
        or record.confirmation_observation_id is None
        or record.provider_message_id is None
    ):
        return False
    try:
        kind = NotificationKind(record.kind)
    except ValueError:
        return False
    threshold = 1 if kind == NotificationKind.INITIAL_CONFIRMATION else 6
    expected_key = f"{kind.value}-v{record.proof_version}-{order.order_reference}"
    expected_payload = {"template": kind.value, "order_reference": order.order_reference}
    if (
        record.confirmation_count < threshold
        or record.message_key != expected_key
        or record.recipient != order.email
        or record.payload != expected_payload
    ):
        return False
    proof = session.scalar(
        select(ProofVersion)
        .where(ProofVersion.order_id == order.id)
        .order_by(ProofVersion.version.desc())
        .limit(1)
    )
    if (
        proof is None
        or proof.version != record.proof_version
        or proof.proof_state not in {"calendar_pending", "bitcoin_verified"}
        or proof.target_digest != order.manifest_digest
        or proof.proof_byte_length != len(proof.proof_bytes)
        or hashlib.sha256(proof.proof_bytes).digest() != proof.proof_sha256
    ):
        return False
    verification = session.scalar(
        select(ProofVerification).where(
            ProofVerification.order_id == order.id,
            ProofVerification.proof_version == proof.version,
        )
    )
    bundle = session.scalar(
        select(ProofBundle).where(
            ProofBundle.order_id == order.id,
            ProofBundle.proof_version == proof.version,
        )
    )
    if (
        verification is None
        or bundle is None
        or not _notification_observation_matches(session, order, record, verification, threshold)
        or (proof.verification_metadata is not None and not _verification_metadata_matches(proof, verification))
    ):
        return False
    return bool(
        bundle.bundle_byte_length == len(bundle.bundle_bytes)
        and hashlib.sha256(bundle.bundle_bytes).digest() == bundle.bundle_sha256
    )


def _notification_observation_matches(
    session: Session,
    order: Order,
    record: OutboxRecord,
    verification: ProofVerification,
    threshold: int,
) -> bool:
    assert record.proof_version is not None
    assert record.confirmation_count is not None
    assert record.confirmation_observation_id is not None
    bound = session.get(BitcoinConfirmationObservation, record.confirmation_observation_id)
    current = session.scalar(_latest_observation_query(order.id, record.proof_version))
    return bool(
        bound is not None
        and current is not None
        and bound.order_id == order.id
        and bound.proof_version == record.proof_version
        and bound.observed_confirmations == record.confirmation_count
        and bound.observed_confirmations >= threshold
        and current.observed_confirmations >= threshold
        and bound.block_height == verification.block_height
        and bound.block_hash == verification.block_hash
        and bound.method == verification.method
        and bound.confirmation_policy == verification.confirmation_policy
        and current.block_height == verification.block_height
        and current.block_hash == verification.block_hash
        and current.method == verification.method
        and current.confirmation_policy == verification.confirmation_policy
    )


def _verification_metadata_matches(proof: ProofVersion, verification: ProofVerification) -> bool:
    metadata = proof.verification_metadata
    if not isinstance(metadata, dict):
        return False
    bitcoin = metadata.get("bitcoin")
    if not isinstance(bitcoin, dict):
        return False
    return bool(
        metadata.get("verification_method") == verification.method
        and _metadata_time_matches(metadata.get("verified_at"), verification.verified_at)
        and bitcoin.get("block_height") == verification.block_height
        and bitcoin.get("block_hash") == verification.block_hash
        and _metadata_time_matches(bitcoin.get("block_time"), verification.block_time)
        and bitcoin.get("confirmation_policy") == verification.confirmation_policy
    )


def _metadata_time_matches(value: object, expected: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    return _aware(parsed) == _aware(expected)


def _authoritative_observation_for_enqueue(
    session: Session,
    order: Order,
    proof_version: int,
    confirmation_count: int,
) -> BitcoinConfirmationObservation:
    proof = session.scalar(
        select(ProofVersion)
        .where(ProofVersion.order_id == order.id)
        .order_by(ProofVersion.version.desc())
        .limit(1)
    )
    verification = session.scalar(
        select(ProofVerification).where(
            ProofVerification.order_id == order.id,
            ProofVerification.proof_version == proof_version,
        )
    )
    observation = session.scalar(_latest_observation_query(order.id, proof_version))
    if (
        proof is None
        or proof.version != proof_version
        or proof.target_digest != order.manifest_digest
        or verification is None
        or observation is None
        or observation.observed_confirmations != confirmation_count
        or observation.block_height != verification.block_height
        or observation.block_hash != verification.block_hash
        or observation.method != verification.method
        or observation.confirmation_policy != verification.confirmation_policy
    ):
        raise ValueError("notification_confirmation_observation_missing")
    return observation


def _latest_observation_query(
    order_id: uuid.UUID,
    proof_version: int,
) -> Select[tuple[BitcoinConfirmationObservation]]:
    return (
        select(BitcoinConfirmationObservation)
        .where(
            BitcoinConfirmationObservation.order_id == order_id,
            BitcoinConfirmationObservation.proof_version == proof_version,
        )
        .order_by(
            BitcoinConfirmationObservation.observed_at.desc(),
            BitcoinConfirmationObservation.created_at.desc(),
            BitcoinConfirmationObservation.id.desc(),
        )
        .limit(1)
    )


def _validate_observation_input(
    proof_version: int,
    event_key: str,
    verification: BitcoinVerification,
) -> None:
    if isinstance(proof_version, bool) or proof_version < 1 or not _SAFE_EVENT_KEY.fullmatch(event_key):
        raise ValueError("confirmation_observation_identity_invalid")
    if (
        not verification.verified
        or verification.confirmations is None
        or verification.confirmations < 1
        or verification.verified_at is None
        or verification.block_height is None
        or verification.block_hash is None
        or verification.block_time is None
        or verification.confirmation_policy is None
    ):
        raise ValueError("confirmation_observation_verification_invalid")


def _verification_matches_result(stored: ProofVerification, result: BitcoinVerification) -> bool:
    return bool(
        stored.method == result.method
        and stored.block_height == result.block_height
        and stored.block_hash == result.block_hash
        and _aware(stored.block_time) == _aware(result.block_time)  # type: ignore[arg-type]
        and stored.confirmation_policy == result.confirmation_policy
    )


def _observation_matches_result(
    observation: BitcoinConfirmationObservation,
    proof_version: int,
    result: BitcoinVerification,
) -> bool:
    return bool(
        observation.proof_version == proof_version
        and observation.observed_confirmations == result.confirmations
        and observation.block_height == result.block_height
        and observation.block_hash == result.block_hash
        and observation.method == result.method
        and observation.confirmation_policy == result.confirmation_policy
        and _aware(observation.observed_at) == _aware(result.verified_at)  # type: ignore[arg-type]
    )


def _project_observation(value: BitcoinConfirmationObservation) -> ConfirmationObservation:
    return ConfirmationObservation(
        id=str(value.id),
        order_id=str(value.order_id),
        proof_version=value.proof_version,
        confirmations=value.observed_confirmations,
        block_height=value.block_height,
        block_hash=value.block_hash,
        method=value.method,
        confirmation_policy=value.confirmation_policy,
        observed_at=_aware(value.observed_at),
        event_key=value.event_key,
    )


def _validate_message(message: OutboxMessage) -> tuple[NotificationKind, int, int]:
    try:
        kind = NotificationKind(message.template)
    except ValueError as error:
        raise ValueError("notification_kind_invalid") from error
    if message.proof_version is None or message.confirmation_count is None:
        raise ValueError("notification_milestone_missing")
    expected = notification_message(
        kind,
        message.order_reference,
        message.recipient,
        message.proof_version,
        message.confirmation_count,
    )
    if message.message_key != expected.message_key:
        raise ValueError("notification_message_key_invalid")
    if dict(message.variables) != {"order_reference": message.order_reference.value}:
        raise ValueError("outbox_variables_not_allowlisted")
    return kind, message.proof_version, message.confirmation_count


def _same_message(
    session: Session,
    existing: OutboxRecord,
    order: Order,
    message: OutboxMessage,
    payload: dict[str, object],
    proof_version: int,
    current_observation: BitcoinConfirmationObservation,
) -> bool:
    bound = (
        session.get(BitcoinConfirmationObservation, existing.confirmation_observation_id)
        if existing.confirmation_observation_id is not None
        else None
    )
    threshold = 1 if message.template == NotificationKind.INITIAL_CONFIRMATION.value else 6
    return (
        existing.order_id == order.id
        and existing.kind == message.template
        and existing.recipient == message.recipient
        and existing.payload == payload
        and existing.proof_version == proof_version
        and existing.confirmation_count is not None
        and existing.confirmation_count >= threshold
        and bound is not None
        and bound.order_id == order.id
        and bound.proof_version == proof_version
        and bound.observed_confirmations == existing.confirmation_count
        and bound.block_height == current_observation.block_height
        and bound.block_hash == current_observation.block_hash
        and bound.method == current_observation.method
        and bound.confirmation_policy == current_observation.confirmation_policy
    )


def _claimed(
    record: OutboxRecord,
    attempt: int,
    worker_id: str,
    lease_token: str,
    lease_until: datetime,
) -> ClaimedNotification:
    if record.proof_version is None or record.confirmation_count is None:
        raise ValueError("stored_notification_milestone_missing")
    order_reference = record.payload.get("order_reference")
    if not isinstance(order_reference, str):
        raise ValueError("stored_notification_payload_invalid")
    kind = NotificationKind(record.kind)
    return ClaimedNotification(
        id=str(record.id),
        message_key=record.message_key,
        order_reference=OrderReference(order_reference),
        kind=kind,
        recipient=record.recipient,
        proof_version=record.proof_version,
        confirmation_count=record.confirmation_count,
        attempt=attempt,
        lease_owner=worker_id,
        lease_token=lease_token,
        lease_until=lease_until,
        idempotency_key=f"resend-{hashlib.sha256(record.message_key.encode('ascii')).hexdigest()}",
    )


def _active_lease(
    session: Session,
    message: ClaimedNotification,
    now: datetime,
) -> tuple[OutboxRecord, NotificationAttempt]:
    now = _aware(now)
    try:
        record_id = uuid.UUID(message.id)
    except ValueError as error:
        raise ValueError("notification_claim_invalid") from error
    record = session.scalar(select(OutboxRecord).where(OutboxRecord.id == record_id).with_for_update())
    if (
        record is None
        or record.state != "leased"
        or record.lease_owner != message.lease_owner
        or record.lease_token != message.lease_token
        or record.attempt_count != message.attempt
        or record.lease_until is None
        or _aware(record.lease_until) < now
        or record.provider_message_id is not None
    ):
        raise ValueError("notification_lease_stale")
    attempt = session.scalar(
        select(NotificationAttempt).where(
            NotificationAttempt.outbox_id == record.id,
            NotificationAttempt.attempt_number == message.attempt,
            NotificationAttempt.lease_token == message.lease_token,
            NotificationAttempt.finished_at.is_(None),
        )
    )
    if attempt is None:
        raise ValueError("notification_attempt_missing")
    return record, attempt


def _finish_attempt(
    attempt: NotificationAttempt,
    now: datetime,
    outcome: str,
    *,
    response_status: int | None = None,
    provider_message_id: str | None = None,
    safe_error_code: str | None = None,
) -> None:
    attempt.finished_at = _aware(now)
    attempt.outcome = outcome
    attempt.response_status = response_status
    attempt.provider_message_id = provider_message_id
    attempt.safe_error_code = safe_error_code


def _validate_worker_and_time(worker_id: str, now: datetime, lease_for: timedelta) -> None:
    if not 1 <= len(worker_id) <= 128 or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("notification_claim_invalid")
    if not timedelta(seconds=1) <= lease_for <= timedelta(hours=1):
        raise ValueError("notification_lease_duration_invalid")


def _validate_safe_error(value: str) -> None:
    if not _SAFE_ERROR_CODE.fullmatch(value):
        raise ValueError("notification_safe_error_code_invalid")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_order_id") from error
