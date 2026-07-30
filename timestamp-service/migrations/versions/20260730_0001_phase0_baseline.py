"""Phase 0 PostgreSQL baseline.

Revision ID: 20260730_0001
Revises: none
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_reference", sa.String(29), nullable=False),
        sa.Column("certificate_reference", sa.String(128), nullable=False),
        sa.Column("manifest_digest", sa.LargeBinary(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("product_version", sa.String(64), nullable=False),
        sa.Column("payment_mode", sa.String(32), nullable=False),
        sa.Column("payment_state", sa.String(32), nullable=False),
        sa.Column("fulfillment_state", sa.String(32), nullable=False),
        sa.Column("consent_terms_version", sa.String(32), nullable=False),
        sa.Column("consent_privacy_version", sa.String(32), nullable=False),
        sa.Column("consent_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkout_session_id", sa.String(128)),
        sa.Column("payment_intent_id", sa.String(128)),
        sa.Column("fulfillment_key", sa.String(160), nullable=False),
        sa.Column("calendar_submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(manifest_digest) = 32", name="ck_orders_digest_32"),
        sa.CheckConstraint("amount_minor >= 0", name="ck_orders_amount_nonnegative"),
        sa.CheckConstraint("length(currency) = 3 AND currency = lower(currency)", name="ck_orders_currency"),
        sa.CheckConstraint("length(email) BETWEEN 1 AND 254", name="ck_orders_email_length"),
        sa.CheckConstraint(
            "payment_state IN ('checkout_open', 'processing', 'paid', 'failed', 'expired', 'refunded', 'disputed')",
            name="ck_orders_payment_state",
        ),
        sa.CheckConstraint(
            "fulfillment_state IN ('awaiting_payment', 'queued', 'stamping', 'calendar_pending', "
            "'bitcoin_verified', 'delivered', 'manual_review')",
            name="ck_orders_fulfillment_state",
        ),
        sa.CheckConstraint(
            "fulfillment_state NOT IN ('queued', 'stamping', 'calendar_pending', 'bitcoin_verified', 'delivered') "
            "OR payment_state IN ('paid', 'refunded', 'disputed')",
            name="ck_orders_paid_before_work",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_session_id"),
        sa.UniqueConstraint("fulfillment_key"),
        sa.UniqueConstraint("order_reference"),
        sa.UniqueConstraint("payment_intent_id"),
    )
    op.create_table(
        "idempotency_requests",
        sa.Column("id", UUID, nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("provider_idempotency_key", sa.String(64), nullable=False),
        sa.Column("provider_price_id", sa.String(128), nullable=False),
        sa.Column("success_url", sa.String(2048), nullable=False),
        sa.Column("cancel_url", sa.String(2048), nullable=False),
        sa.Column("checkout_state", sa.String(16), nullable=False),
        sa.Column("checkout_lease_id", sa.String(36)),
        sa.Column("checkout_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", JSON),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(key_hash) = 32", name="ck_idempotency_key_hash_32"),
        sa.CheckConstraint("length(request_hash) = 32", name="ck_idempotency_request_hash_32"),
        sa.CheckConstraint(
            "checkout_state IN ('reserved', 'processing', 'completed')",
            name="ck_idempotency_checkout_state",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", "key_hash", name="uq_idempotency_endpoint_key"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("provider_idempotency_key"),
    )
    op.create_table(
        "stripe_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("stripe_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(payload_sha256) = 32", name="ck_stripe_events_payload_hash_32"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )
    op.create_table(
        "order_tokens",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("pepper_version", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(token_hash) = 32", name="ck_order_tokens_hash_32"),
        sa.CheckConstraint("version > 0", name="ck_order_tokens_version_positive"),
        sa.CheckConstraint("pepper_version > 0", name="ck_order_tokens_pepper_version_positive"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "version", name="uq_order_tokens_order_version"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_order_tokens_order_id", "order_tokens", ["order_id"])
    op.create_table(
        "rate_counters",
        sa.Column("id", UUID, nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(key_hash) = 32", name="ck_rate_counter_key_hash_32"),
        sa.CheckConstraint("request_count > 0", name="ck_rate_counter_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", "key_hash", "window_started_at", name="uq_rate_counter_window"),
    )
    op.create_table(
        "durable_jobs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("job_key", sa.String(160), nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0 AND max_attempts > 0", name="ck_jobs_attempts"),
        sa.CheckConstraint("lease_until IS NULL OR lease_owner IS NOT NULL", name="ck_jobs_lease_owner"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key"),
    )
    op.create_index("ix_durable_jobs_available_at", "durable_jobs", ["available_at"])
    op.create_index("ix_durable_jobs_lease_until", "durable_jobs", ["lease_until"])
    op.create_index("ix_durable_jobs_order_id", "durable_jobs", ["order_id"])
    op.create_table(
        "job_attempts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(32)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["durable_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_number"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_table(
        "proof_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target_digest", sa.LargeBinary(), nullable=False),
        sa.Column("proof_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("proof_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("proof_byte_length", sa.Integer(), nullable=False),
        sa.Column("proof_state", sa.String(32), nullable=False),
        sa.Column("calendar_submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_metadata", JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_proof_version_positive"),
        sa.CheckConstraint("length(target_digest) = 32", name="ck_proof_target_32"),
        sa.CheckConstraint("length(proof_sha256) = 32", name="ck_proof_hash_32"),
        sa.CheckConstraint(
            "proof_byte_length BETWEEN 1 AND 262144 "
            "AND proof_byte_length = octet_length(proof_bytes)",
            name="ck_proof_length",
        ),
        sa.CheckConstraint(
            "(proof_state = 'calendar_pending' AND verification_metadata IS NULL) OR "
            "(proof_state = 'bitcoin_verified' AND verification_metadata IS NOT NULL)",
            name="ck_proof_state_metadata",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "version", name="uq_proof_order_version"),
    )
    op.create_index("ix_proof_versions_order_id", "proof_versions", ["order_id"])
    op.create_table(
        "proof_verifications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("proof_version", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(128), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("block_height", sa.Integer(), nullable=False),
        sa.Column("block_hash", sa.String(64), nullable=False),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_policy", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proof_version > 0", name="ck_proof_verification_version_positive"),
        sa.CheckConstraint("block_height >= 0", name="ck_proof_verification_height"),
        sa.CheckConstraint(
            "length(block_hash) = 64 AND block_hash = lower(block_hash)",
            name="ck_proof_verification_block_hash",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "proof_version"],
            ["proof_versions.order_id", "proof_versions.version"],
            ondelete="CASCADE",
            name="fk_proof_verification_proof_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "proof_version", name="uq_proof_verification_order_version"),
    )
    op.create_index("ix_proof_verifications_order_id", "proof_verifications", ["order_id"])
    op.create_table(
        "proof_bundles",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("proof_version", sa.Integer(), nullable=False),
        sa.Column("bundle_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("bundle_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("bundle_byte_length", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proof_version > 0", name="ck_proof_bundle_version_positive"),
        sa.CheckConstraint("length(bundle_sha256) = 32", name="ck_proof_bundle_hash_32"),
        sa.CheckConstraint(
            "bundle_byte_length > 0 AND bundle_byte_length = length(bundle_bytes) "
            "AND bundle_byte_length <= 12582912",
            name="ck_proof_bundle_length",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "proof_version"],
            ["proof_versions.order_id", "proof_versions.version"],
            ondelete="CASCADE",
            name="fk_proof_bundle_proof_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "proof_version", name="uq_proof_bundle_order_version"),
    )
    op.create_index("ix_proof_bundles_order_id", "proof_bundles", ["order_id"])
    op.create_table(
        "state_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("previous_payment_state", sa.String(32)),
        sa.Column("payment_state", sa.String(32), nullable=False),
        sa.Column("previous_fulfillment_state", sa.String(32)),
        sa.Column("fulfillment_state", sa.String(32), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "event_key", name="uq_state_event_key"),
        sa.UniqueConstraint("order_id", "sequence", name="uq_state_event_sequence"),
    )
    op.create_index("ix_state_events_order_id", "state_events", ["order_id"])
    op.create_table(
        "outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("message_key", sa.String(160), nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(128)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempts"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_key"),
        sa.UniqueConstraint("provider_message_id"),
    )
    op.create_index("ix_outbox_available_at", "outbox", ["available_at"])
    op.create_index("ix_outbox_order_id", "outbox", ["order_id"])

    op.execute(
        """
        CREATE FUNCTION reject_append_only_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("proof_versions", "proof_verifications", "proof_bundles", "state_events"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )
    op.execute(
        """
        CREATE FUNCTION reject_order_snapshot_change() RETURNS trigger AS $$
        BEGIN
          IF ROW(NEW.order_reference, NEW.certificate_reference, NEW.manifest_digest, NEW.email,
                 NEW.amount_minor, NEW.currency, NEW.product_version, NEW.payment_mode,
                 NEW.consent_terms_version, NEW.consent_privacy_version, NEW.consent_accepted_at,
                 NEW.fulfillment_key)
             IS DISTINCT FROM
             ROW(OLD.order_reference, OLD.certificate_reference, OLD.manifest_digest, OLD.email,
                 OLD.amount_minor, OLD.currency, OLD.product_version, OLD.payment_mode,
                 OLD.consent_terms_version, OLD.consent_privacy_version, OLD.consent_accepted_at,
                 OLD.fulfillment_key) THEN
            RAISE EXCEPTION 'immutable order snapshot cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER orders_immutable_snapshot BEFORE UPDATE ON orders "
        "FOR EACH ROW EXECUTE FUNCTION reject_order_snapshot_change()"
    )
    op.execute(
        """
        CREATE FUNCTION enforce_order_state_transition() RETURNS trigger AS $$
        BEGIN
          IF NEW.payment_state IS DISTINCT FROM OLD.payment_state AND NOT (
            (OLD.payment_state = 'checkout_open' AND NEW.payment_state IN
              ('processing', 'paid', 'failed', 'expired', 'refunded', 'disputed')) OR
            (OLD.payment_state = 'processing' AND NEW.payment_state IN
              ('paid', 'failed', 'expired', 'refunded', 'disputed')) OR
            (OLD.payment_state = 'paid' AND NEW.payment_state IN ('refunded', 'disputed')) OR
            (OLD.payment_state = 'disputed' AND NEW.payment_state = 'refunded')
          ) THEN
            RAISE EXCEPTION 'illegal payment state transition: % -> %', OLD.payment_state, NEW.payment_state;
          END IF;

          IF NEW.fulfillment_state IS DISTINCT FROM OLD.fulfillment_state AND NOT (
            (OLD.fulfillment_state = 'awaiting_payment' AND NEW.fulfillment_state IN ('queued', 'manual_review')) OR
            (OLD.fulfillment_state = 'queued' AND NEW.fulfillment_state IN ('stamping', 'manual_review')) OR
            (OLD.fulfillment_state = 'stamping' AND NEW.fulfillment_state IN ('calendar_pending', 'manual_review')) OR
            (OLD.fulfillment_state = 'calendar_pending' AND NEW.fulfillment_state IN
              ('bitcoin_verified', 'manual_review')) OR
            (OLD.fulfillment_state = 'bitcoin_verified' AND NEW.fulfillment_state IN ('delivered', 'manual_review')) OR
            (OLD.fulfillment_state = 'delivered' AND NEW.fulfillment_state = 'manual_review')
          ) THEN
            RAISE EXCEPTION 'illegal fulfillment state transition: % -> %',
              OLD.fulfillment_state, NEW.fulfillment_state;
          END IF;

          IF OLD.fulfillment_state IS DISTINCT FROM NEW.fulfillment_state
             AND NEW.fulfillment_state = 'queued' AND NEW.payment_state <> 'paid' THEN
            RAISE EXCEPTION 'fulfillment cannot be queued before payment';
          END IF;
          IF OLD.fulfillment_state IS DISTINCT FROM NEW.fulfillment_state
             AND NEW.fulfillment_state = 'bitcoin_verified'
             AND NOT EXISTS (
               SELECT 1
               FROM proof_versions latest
               JOIN proof_verifications verification
                 ON verification.order_id = latest.order_id
                AND verification.proof_version = latest.version
               WHERE latest.order_id = NEW.id
                 AND latest.version = (
                   SELECT MAX(candidate.version) FROM proof_versions candidate WHERE candidate.order_id = NEW.id
                 )
             ) THEN
            RAISE EXCEPTION 'bitcoin verification evidence is required';
          END IF;
          IF OLD.fulfillment_state IS DISTINCT FROM NEW.fulfillment_state
             AND NEW.fulfillment_state = 'delivered'
             AND NOT EXISTS (
               SELECT 1
               FROM proof_versions latest
               JOIN proof_verifications verification
                 ON verification.order_id = latest.order_id
                AND verification.proof_version = latest.version
               JOIN proof_bundles bundle
                 ON bundle.order_id = latest.order_id
                AND bundle.proof_version = latest.version
               JOIN outbox sender
                 ON sender.order_id = latest.order_id
                AND sender.message_key =
                  'timestamp-complete-v' || latest.version::text || '-' || NEW.order_reference
               WHERE latest.order_id = NEW.id
                 AND latest.version = (
                   SELECT MAX(candidate.version) FROM proof_versions candidate WHERE candidate.order_id = NEW.id
                 )
                 AND sender.kind = 'timestamp-complete'
                 AND sender.state = 'delivered'
                 AND sender.provider_message_id IS NOT NULL
                 AND sender.delivered_at IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'proof bundle and sender evidence is required';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER orders_state_transition BEFORE UPDATE ON orders "
        "FOR EACH ROW EXECUTE FUNCTION enforce_order_state_transition()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS orders_state_transition ON orders")
    op.execute("DROP FUNCTION IF EXISTS enforce_order_state_transition()")
    op.execute("DROP TRIGGER IF EXISTS orders_immutable_snapshot ON orders")
    op.execute("DROP FUNCTION IF EXISTS reject_order_snapshot_change()")
    for table in ("proof_versions", "proof_verifications", "proof_bundles", "state_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_append_only_change()")
    op.drop_index("ix_outbox_order_id", table_name="outbox")
    op.drop_index("ix_outbox_available_at", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_state_events_order_id", table_name="state_events")
    op.drop_table("state_events")
    op.drop_index("ix_proof_versions_order_id", table_name="proof_versions")
    op.drop_index("ix_proof_bundles_order_id", table_name="proof_bundles")
    op.drop_table("proof_bundles")
    op.drop_index("ix_proof_verifications_order_id", table_name="proof_verifications")
    op.drop_table("proof_verifications")
    op.drop_table("proof_versions")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_durable_jobs_order_id", table_name="durable_jobs")
    op.drop_index("ix_durable_jobs_lease_until", table_name="durable_jobs")
    op.drop_index("ix_durable_jobs_available_at", table_name="durable_jobs")
    op.drop_table("durable_jobs")
    op.drop_index("ix_order_tokens_order_id", table_name="order_tokens")
    op.drop_table("order_tokens")
    op.drop_table("rate_counters")
    op.drop_table("stripe_events")
    op.drop_table("idempotency_requests")
    op.drop_table("orders")
