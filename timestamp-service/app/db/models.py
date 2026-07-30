from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base

JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("length(manifest_digest) = 32", name="ck_orders_digest_32"),
        CheckConstraint("amount_minor >= 0", name="ck_orders_amount_nonnegative"),
        CheckConstraint("length(currency) = 3 AND currency = lower(currency)", name="ck_orders_currency"),
        CheckConstraint("length(email) BETWEEN 1 AND 254", name="ck_orders_email_length"),
        CheckConstraint(
            "payment_state IN ('checkout_open', 'processing', 'paid', 'failed', 'expired', 'refunded', 'disputed')",
            name="ck_orders_payment_state",
        ),
        CheckConstraint(
            "fulfillment_state IN ('awaiting_payment', 'queued', 'stamping', 'calendar_pending', "
            "'bitcoin_verified', 'delivered', 'manual_review')",
            name="ck_orders_fulfillment_state",
        ),
        CheckConstraint(
            "fulfillment_state NOT IN ('queued', 'stamping', 'calendar_pending', 'bitcoin_verified', 'delivered') "
            "OR payment_state IN ('paid', 'refunded', 'disputed')",
            name="ck_orders_paid_before_work",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_reference: Mapped[str] = mapped_column(String(29), unique=True, nullable=False)
    certificate_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    product_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payment_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_state: Mapped[str] = mapped_column(String(32), nullable=False)
    fulfillment_state: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_terms_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_privacy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checkout_session_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    fulfillment_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    calendar_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderToken(Base, TimestampMixin):
    __tablename__ = "order_tokens"
    __table_args__ = (
        UniqueConstraint("order_id", "version", name="uq_order_tokens_order_version"),
        CheckConstraint("length(token_hash) = 32", name="ck_order_tokens_hash_32"),
        CheckConstraint("version > 0", name="ck_order_tokens_version_positive"),
        CheckConstraint("pepper_version > 0", name="ck_order_tokens_pepper_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    pepper_version: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRequest(Base, TimestampMixin):
    __tablename__ = "idempotency_requests"
    __table_args__ = (
        UniqueConstraint("endpoint", "key_hash", name="uq_idempotency_endpoint_key"),
        CheckConstraint("length(key_hash) = 32", name="ck_idempotency_key_hash_32"),
        CheckConstraint("length(request_hash) = 32", name="ck_idempotency_request_hash_32"),
        CheckConstraint(
            "checkout_state IN ('reserved', 'processing', 'completed')",
            name="ck_idempotency_checkout_state",
        ),
        CheckConstraint(
            "(checkout_state = 'reserved' AND checkout_lease_id IS NULL AND checkout_lease_expires_at IS NULL "
            "AND response_status IS NULL AND response_body IS NULL AND completed_at IS NULL) OR "
            "(checkout_state = 'processing' AND checkout_lease_id IS NOT NULL "
            "AND checkout_lease_expires_at IS NOT NULL AND response_status IS NULL "
            "AND response_body IS NULL AND completed_at IS NULL) OR "
            "(checkout_state = 'completed' AND checkout_lease_id IS NOT NULL "
            "AND checkout_lease_expires_at IS NOT NULL AND response_status IS NOT NULL "
            "AND response_body IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_idempotency_completion",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider_idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider_price_id: Mapped[str] = mapped_column(String(128), nullable=False)
    success_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    cancel_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    checkout_state: Mapped[str] = mapped_column(String(16), nullable=False)
    checkout_lease_id: Mapped[str | None] = mapped_column(String(36))
    checkout_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONType)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateCounter(Base):
    __tablename__ = "rate_counters"
    __table_args__ = (
        UniqueConstraint("endpoint", "key_hash", "window_started_at", name="uq_rate_counter_window"),
        CheckConstraint("length(key_hash) = 32", name="ck_rate_counter_key_hash_32"),
        CheckConstraint("request_count > 0", name="ck_rate_counter_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)


class StripeEvent(Base, TimestampMixin):
    __tablename__ = "stripe_events"
    __table_args__ = (CheckConstraint("length(payload_sha256) = 32", name="ck_stripe_events_payload_hash_32"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    stripe_event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))


class DurableJob(Base, TimestampMixin):
    __tablename__ = "durable_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0 AND max_attempts > 0", name="ck_jobs_attempts"),
        CheckConstraint("lease_until IS NULL OR lease_owner IS NOT NULL", name="ck_jobs_lease_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobAttempt(Base, TimestampMixin):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("durable_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))


class ProofVersion(Base, TimestampMixin):
    __tablename__ = "proof_versions"
    __table_args__ = (
        UniqueConstraint("order_id", "version", name="uq_proof_order_version"),
        CheckConstraint("version > 0", name="ck_proof_version_positive"),
        CheckConstraint("length(target_digest) = 32", name="ck_proof_target_32"),
        CheckConstraint("length(proof_sha256) = 32", name="ck_proof_hash_32"),
        CheckConstraint(
            "proof_byte_length BETWEEN 1 AND 262144 AND proof_byte_length = length(proof_bytes)",
            name="ck_proof_length",
        ),
        CheckConstraint(
            "(proof_state = 'calendar_pending' AND verification_metadata IS NULL) OR "
            "(proof_state = 'bitcoin_verified' AND verification_metadata IS NOT NULL)",
            name="ck_proof_state_metadata",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    proof_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    proof_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    proof_byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_state: Mapped[str] = mapped_column(String(32), nullable=False)
    calendar_submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONType)


class ProofVerification(Base, TimestampMixin):
    __tablename__ = "proof_verifications"
    __table_args__ = (
        UniqueConstraint("order_id", "proof_version", name="uq_proof_verification_order_version"),
        CheckConstraint("proof_version > 0", name="ck_proof_verification_version_positive"),
        CheckConstraint("block_height >= 0", name="ck_proof_verification_height"),
        CheckConstraint(
            "length(block_hash) = 64 AND block_hash = lower(block_hash)",
            name="ck_proof_verification_block_hash",
        ),
        ForeignKeyConstraint(
            ["order_id", "proof_version"],
            ["proof_versions.order_id", "proof_versions.version"],
            ondelete="CASCADE",
            name="fk_proof_verification_proof_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    proof_version: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    block_height: Mapped[int] = mapped_column(Integer, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    block_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_policy: Mapped[str] = mapped_column(String(128), nullable=False)


class ProofBundle(Base, TimestampMixin):
    __tablename__ = "proof_bundles"
    __table_args__ = (
        UniqueConstraint("order_id", "proof_version", name="uq_proof_bundle_order_version"),
        CheckConstraint("proof_version > 0", name="ck_proof_bundle_version_positive"),
        CheckConstraint("length(bundle_sha256) = 32", name="ck_proof_bundle_hash_32"),
        CheckConstraint(
            "bundle_byte_length > 0 AND bundle_byte_length = length(bundle_bytes) "
            "AND bundle_byte_length <= 12582912",
            name="ck_proof_bundle_length",
        ),
        ForeignKeyConstraint(
            ["order_id", "proof_version"],
            ["proof_versions.order_id", "proof_versions.version"],
            ondelete="CASCADE",
            name="fk_proof_bundle_proof_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    proof_version: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    bundle_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    bundle_byte_length: Mapped[int] = mapped_column(Integer, nullable=False)


class StateEvent(Base, TimestampMixin):
    __tablename__ = "state_events"
    __table_args__ = (
        UniqueConstraint("order_id", "sequence", name="uq_state_event_sequence"),
        UniqueConstraint("order_id", "event_key", name="uq_state_event_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_payment_state: Mapped[str | None] = mapped_column(String(32))
    payment_state: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_fulfillment_state: Mapped[str | None] = mapped_column(String(32))
    fulfillment_state: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONType, nullable=False)


class OutboxMessage(Base, TimestampMixin):
    __tablename__ = "outbox"
    __table_args__ = (CheckConstraint("attempt_count >= 0", name="ck_outbox_attempts"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONType, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))


_IMMUTABLE_ORDER_FIELDS = frozenset(
    {
        "order_reference",
        "certificate_reference",
        "manifest_digest",
        "email",
        "amount_minor",
        "currency",
        "product_version",
        "payment_mode",
        "consent_terms_version",
        "consent_privacy_version",
        "consent_accepted_at",
        "fulfillment_key",
    }
)


@event.listens_for(Order, "before_update")
def reject_order_snapshot_mutation(_mapper: object, _connection: object, target: Order) -> None:
    state = inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _IMMUTABLE_ORDER_FIELDS):
        raise ValueError("immutable order snapshot fields cannot be changed")


@event.listens_for(ProofVersion, "before_update")
@event.listens_for(ProofVerification, "before_update")
@event.listens_for(ProofBundle, "before_update")
@event.listens_for(ProofVersion, "before_delete")
@event.listens_for(ProofVerification, "before_delete")
@event.listens_for(ProofBundle, "before_delete")
def reject_proof_evidence_mutation(_mapper: object, _connection: object, _target: object) -> None:
    raise ValueError("proof evidence is append-only")
