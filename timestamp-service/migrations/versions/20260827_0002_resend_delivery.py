"""Add durable Resend notification delivery evidence.

Revision ID: 20260827_0002
Revises: 20260730_0001
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0002"
down_revision: str | None = "20260730_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5(orders.id::text || '-20260827-legacy-delivery-manual-review')::uuid,
               orders.id,
               COALESCE((
                 SELECT MAX(existing.sequence) FROM state_events existing WHERE existing.order_id = orders.id
               ), 0) + 1,
               'migration-20260827-legacy-delivery-manual-review',
               'delivery_migration',
               orders.payment_state, orders.payment_state,
               'delivered', 'manual_review',
               jsonb_build_object(
                 'reason', 'legacy_delivery_without_signed_resend_evidence',
                 'migration_revision', '20260827_0002'
               ),
               now()
        FROM orders
        WHERE orders.fulfillment_state = 'delivered'
        ON CONFLICT (order_id, event_key) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE orders
        SET fulfillment_state = 'manual_review', updated_at = now()
        WHERE fulfillment_state = 'delivered'
        """
    )

    op.create_table(
        "bitcoin_confirmation_observations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("proof_version", sa.Integer(), nullable=False),
        sa.Column("observed_confirmations", sa.Integer(), nullable=False),
        sa.Column("block_height", sa.Integer(), nullable=False),
        sa.Column("block_hash", sa.String(64), nullable=False),
        sa.Column("method", sa.String(128), nullable=False),
        sa.Column("confirmation_policy", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proof_version > 0", name="ck_bitcoin_confirmation_proof_version"),
        sa.CheckConstraint("observed_confirmations > 0", name="ck_bitcoin_confirmation_count"),
        sa.CheckConstraint("block_height >= 0", name="ck_bitcoin_confirmation_height"),
        sa.CheckConstraint(
            "length(block_hash) = 64 AND block_hash = lower(block_hash)",
            name="ck_bitcoin_confirmation_block_hash",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "proof_version"],
            ["proof_versions.order_id", "proof_versions.version"],
            ondelete="CASCADE",
            name="fk_bitcoin_confirmation_proof_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "event_key", name="uq_bitcoin_confirmation_event_key"),
    )
    op.create_index(
        "ix_bitcoin_confirmation_observations_order_id",
        "bitcoin_confirmation_observations",
        ["order_id"],
    )
    op.execute(
        """
        INSERT INTO bitcoin_confirmation_observations (
          id, order_id, proof_version, observed_confirmations, block_height,
          block_hash, method, confirmation_policy, observed_at, event_key, created_at
        )
        SELECT md5(verification.order_id::text || '-' || verification.proof_version::text)::uuid,
               verification.order_id, verification.proof_version, 1, verification.block_height,
               verification.block_hash, verification.method, verification.confirmation_policy,
               verification.verified_at,
               'legacy-verification-v' || verification.proof_version::text,
               verification.created_at
        FROM proof_verifications verification
        """
    )
    op.execute(
        "CREATE TRIGGER bitcoin_confirmation_observations_append_only "
        "BEFORE UPDATE OR DELETE ON bitcoin_confirmation_observations "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
    )

    op.drop_constraint("ck_outbox_attempts", "outbox", type_="check")
    op.add_column("outbox", sa.Column("max_attempts", sa.Integer(), server_default="12", nullable=False))
    op.add_column("outbox", sa.Column("lease_token", sa.String(36)))
    op.add_column("outbox", sa.Column("proof_version", sa.Integer()))
    op.add_column("outbox", sa.Column("confirmation_count", sa.Integer()))
    op.add_column("outbox", sa.Column("confirmation_observation_id", UUID))
    op.add_column("outbox", sa.Column("accepted_at", sa.DateTime(timezone=True)))
    op.add_column(
        "outbox",
        sa.Column(
            "idempotency_expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now() + INTERVAL '24 hours'"),
        ),
    )
    op.add_column(
        "outbox",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_foreign_key(
        "fk_outbox_confirmation_observation",
        "outbox",
        "bitcoin_confirmation_observations",
        ["confirmation_observation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_outbox_confirmation_observation_id",
        "outbox",
        ["confirmation_observation_id"],
    )
    op.execute(
        """
        UPDATE outbox
        SET accepted_at = COALESCE(delivered_at, created_at)
        WHERE provider_message_id IS NOT NULL
        """
    )
    op.execute("UPDATE outbox SET updated_at = created_at")
    op.execute("UPDATE outbox SET idempotency_expires_at = created_at + INTERVAL '24 hours'")
    op.execute(
        """
        UPDATE outbox sender
        SET kind = 'bitcoin-confirmed-initial',
            message_key = 'bitcoin-confirmed-initial-v' || latest.version::text || '-' || orders.order_reference,
            payload = jsonb_build_object(
              'template', 'bitcoin-confirmed-initial',
              'order_reference', orders.order_reference
            ),
            proof_version = latest.version,
            confirmation_count = 1,
            confirmation_observation_id = observation.id,
            state = CASE
              WHEN sender.provider_message_id IS NOT NULL THEN 'accepted'
              WHEN orders.fulfillment_state = 'manual_review' THEN 'dead_letter'
              ELSE 'available'
            END,
            accepted_at = CASE
              WHEN sender.provider_message_id IS NOT NULL
              THEN COALESCE(sender.accepted_at, sender.delivered_at, sender.created_at)
              ELSE NULL
            END,
            delivered_at = NULL,
            lease_owner = NULL,
            lease_until = NULL,
            lease_token = NULL,
            available_at = now(),
            idempotency_expires_at = now() + INTERVAL '24 hours',
            safe_error_code = CASE
              WHEN orders.fulfillment_state = 'manual_review' THEN 'legacy_delivery_manual_review'
              ELSE NULL
            END,
            updated_at = now()
        FROM orders, proof_versions latest, bitcoin_confirmation_observations observation
        WHERE sender.kind = 'timestamp-complete'
          AND sender.order_id = orders.id
          AND latest.order_id = orders.id
          AND latest.version = (
            SELECT MAX(candidate.version) FROM proof_versions candidate WHERE candidate.order_id = orders.id
          )
          AND observation.order_id = orders.id
          AND observation.proof_version = latest.version
          AND observation.observed_confirmations >= 1
        """
    )
    op.execute(
        """
        UPDATE outbox
        SET state = 'dead_letter',
            lease_owner = NULL,
            lease_until = NULL,
            lease_token = NULL,
            safe_error_code = 'legacy_evidence_missing',
            updated_at = now()
        WHERE kind = 'timestamp-complete'
        """
    )
    op.alter_column("outbox", "idempotency_expires_at", nullable=False)
    op.alter_column("outbox", "updated_at", nullable=False)
    op.create_check_constraint(
        "ck_outbox_attempts",
        "outbox",
        "attempt_count >= 0 AND max_attempts > 0",
    )
    op.create_check_constraint(
        "ck_outbox_lease_owner",
        "outbox",
        "lease_until IS NULL OR lease_owner IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_outbox_proof_version_positive",
        "outbox",
        "proof_version IS NULL OR proof_version > 0",
    )
    op.create_check_constraint(
        "ck_outbox_confirmation_count_positive",
        "outbox",
        "confirmation_count IS NULL OR confirmation_count > 0",
    )
    op.create_index("ix_outbox_idempotency_expires_at", "outbox", ["idempotency_expires_at"])

    op.create_table(
        "notification_attempts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("outbox_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(32)),
        sa.Column("response_status", sa.Integer()),
        sa.Column("provider_message_id", sa.String(128)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_notification_attempt_positive"),
        sa.CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_notification_attempt_response_status",
        ),
        sa.ForeignKeyConstraint(["outbox_id"], ["outbox.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outbox_id", "attempt_number", name="uq_notification_attempt_number"),
    )
    op.create_index("ix_notification_attempts_outbox_id", "notification_attempts", ["outbox_id"])

    op.create_table(
        "resend_webhook_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("svix_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("provider_message_id", sa.String(128), nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(payload_sha256) = 32", name="ck_resend_webhook_payload_hash_32"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("svix_event_id"),
    )
    op.create_index(
        "ix_resend_webhook_events_provider_message_id",
        "resend_webhook_events",
        ["provider_message_id"],
    )
    op.execute(
        "CREATE TRIGGER resend_webhook_events_append_only BEFORE UPDATE OR DELETE ON resend_webhook_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
    )
    op.execute(_delivery_guard_function("resend"))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM resend_webhook_events resend_event
            LEFT JOIN outbox sender
              ON sender.provider_message_id = resend_event.provider_message_id
            WHERE sender.id IS NULL
          ) THEN
            RAISE EXCEPTION
              'unsafe delivery downgrade: unmatched Resend webhook evidence cannot be represented';
          END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5(orders.id::text || '-delivery-downgrade-unsupported-delivered')::uuid,
               orders.id,
               COALESCE((
                 SELECT MAX(existing.sequence) FROM state_events existing WHERE existing.order_id = orders.id
               ), 0) + 1,
               'downgrade-delivery-unsupported-delivered',
               'delivery_downgrade',
               orders.payment_state, orders.payment_state,
               'delivered', 'manual_review',
               jsonb_build_object(
                 'reason', 'delivered_order_missing_signed_initial_notice',
                 'migration_revision', '20260827_0002'
               ),
               now()
        FROM orders
        WHERE orders.fulfillment_state = 'delivered'
          AND NOT EXISTS (
            SELECT 1
            FROM outbox sender
            JOIN resend_webhook_events resend_event
              ON resend_event.provider_message_id = sender.provider_message_id
             AND resend_event.event_type = 'email.delivered'
            WHERE sender.order_id = orders.id
              AND sender.kind = 'bitcoin-confirmed-initial'
              AND sender.state = 'delivered'
              AND sender.accepted_at IS NOT NULL
              AND sender.delivered_at IS NOT NULL
          )
        ON CONFLICT (order_id, event_key) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE orders
        SET fulfillment_state = 'manual_review', updated_at = now()
        WHERE fulfillment_state = 'delivered'
          AND NOT EXISTS (
            SELECT 1
            FROM outbox sender
            JOIN resend_webhook_events resend_event
              ON resend_event.provider_message_id = sender.provider_message_id
             AND resend_event.event_type = 'email.delivered'
            WHERE sender.order_id = orders.id
              AND sender.kind = 'bitcoin-confirmed-initial'
              AND sender.state = 'delivered'
              AND sender.accepted_at IS NOT NULL
              AND sender.delivered_at IS NOT NULL
          )
        """
    )
    for statement in _downgrade_evidence_statements():
        op.execute(statement)
    op.execute(
        """
        UPDATE outbox sender
        SET kind = 'timestamp-complete',
            message_key = 'timestamp-complete-v' || sender.proof_version::text || '-' || orders.order_reference,
            payload = jsonb_build_object('order_reference', orders.order_reference),
            state = CASE
              WHEN sender.state = 'delivered'
               AND sender.accepted_at IS NOT NULL
               AND sender.delivered_at IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM resend_webhook_events resend_event
                 WHERE resend_event.provider_message_id = sender.provider_message_id
                   AND resend_event.event_type = 'email.delivered'
               ) THEN 'delivered'
              WHEN sender.state = 'delivered' THEN 'dead_letter'
              ELSE sender.state
            END,
            delivered_at = CASE
              WHEN sender.state = 'delivered'
               AND sender.accepted_at IS NOT NULL
               AND sender.delivered_at IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM resend_webhook_events resend_event
                 WHERE resend_event.provider_message_id = sender.provider_message_id
                   AND resend_event.event_type = 'email.delivered'
               ) THEN sender.delivered_at
              ELSE NULL
            END,
            safe_error_code = CASE
              WHEN sender.state = 'delivered'
               AND NOT EXISTS (
                 SELECT 1 FROM resend_webhook_events resend_event
                 WHERE resend_event.provider_message_id = sender.provider_message_id
                   AND resend_event.event_type = 'email.delivered'
               ) THEN 'downgrade_delivery_evidence_missing'
              ELSE sender.safe_error_code
            END
        FROM orders
        WHERE sender.order_id = orders.id
          AND sender.kind = 'bitcoin-confirmed-initial'
          AND sender.proof_version IS NOT NULL
        """
    )
    op.execute("DROP TRIGGER IF EXISTS resend_webhook_events_append_only ON resend_webhook_events")
    op.execute(
        """
        UPDATE outbox
        SET state = 'dead_letter',
            delivered_at = NULL,
            safe_error_code = 'downgrade_final_notice_unrepresentable'
        WHERE kind = 'bitcoin-confirmed-final'
        """
    )
    op.execute(_delivery_guard_function("legacy"))
    op.drop_index("ix_resend_webhook_events_provider_message_id", table_name="resend_webhook_events")
    op.drop_table("resend_webhook_events")
    op.drop_index("ix_notification_attempts_outbox_id", table_name="notification_attempts")
    op.drop_table("notification_attempts")
    op.drop_index("ix_outbox_confirmation_observation_id", table_name="outbox")
    op.drop_constraint("fk_outbox_confirmation_observation", "outbox", type_="foreignkey")
    op.drop_index("ix_outbox_idempotency_expires_at", table_name="outbox")
    op.drop_constraint("ck_outbox_confirmation_count_positive", "outbox", type_="check")
    op.drop_constraint("ck_outbox_proof_version_positive", "outbox", type_="check")
    op.drop_constraint("ck_outbox_lease_owner", "outbox", type_="check")
    op.drop_constraint("ck_outbox_attempts", "outbox", type_="check")
    for column in (
        "updated_at",
        "idempotency_expires_at",
        "accepted_at",
        "confirmation_count",
        "confirmation_observation_id",
        "proof_version",
        "lease_token",
        "max_attempts",
    ):
        op.drop_column("outbox", column)
    op.create_check_constraint("ck_outbox_attempts", "outbox", "attempt_count >= 0")
    op.execute(
        "DROP TRIGGER IF EXISTS bitcoin_confirmation_observations_append_only "
        "ON bitcoin_confirmation_observations"
    )
    op.drop_index(
        "ix_bitcoin_confirmation_observations_order_id",
        table_name="bitcoin_confirmation_observations",
    )
    op.drop_table("bitcoin_confirmation_observations")


def _downgrade_evidence_statements() -> tuple[str, ...]:
    return (
        """
        WITH candidates AS (
          SELECT observation.*,
                 orders.payment_state,
                 orders.fulfillment_state,
                 COALESCE((
                   SELECT MAX(existing.sequence)
                   FROM state_events existing
                   WHERE existing.order_id = observation.order_id
                 ), 0) AS base_sequence,
                 ROW_NUMBER() OVER (
                   PARTITION BY observation.order_id
                   ORDER BY observation.created_at, observation.id
                 ) AS ordinal
          FROM bitcoin_confirmation_observations observation
          JOIN orders ON orders.id = observation.order_id
        )
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5('downgrade-confirmation-' || candidates.id::text)::uuid,
               candidates.order_id,
               candidates.base_sequence + candidates.ordinal::integer,
               'downgrade-confirmation-' || candidates.id::text,
               'delivery_downgrade',
               candidates.payment_state, candidates.payment_state,
               candidates.fulfillment_state, candidates.fulfillment_state,
               jsonb_build_object(
                 'evidence_kind', 'bitcoin_confirmation_observation',
                 'observation_id', candidates.id,
                 'proof_version', candidates.proof_version,
                 'observed_confirmations', candidates.observed_confirmations,
                 'block_height', candidates.block_height,
                 'block_hash', candidates.block_hash,
                 'method', candidates.method,
                 'confirmation_policy', candidates.confirmation_policy,
                 'observed_at', candidates.observed_at,
                 'original_event_key', candidates.event_key,
                 'migration_revision', '20260827_0002'
               ),
               candidates.created_at
        FROM candidates
        ON CONFLICT (order_id, event_key) DO NOTHING
        """,
        """
        WITH candidates AS (
          SELECT resend_event.*,
                 sender.order_id,
                 orders.payment_state,
                 orders.fulfillment_state,
                 COALESCE((
                   SELECT MAX(existing.sequence)
                   FROM state_events existing
                   WHERE existing.order_id = sender.order_id
                 ), 0) AS base_sequence,
                 ROW_NUMBER() OVER (
                   PARTITION BY sender.order_id
                   ORDER BY resend_event.created_at, resend_event.id
                 ) AS ordinal
          FROM resend_webhook_events resend_event
          JOIN outbox sender ON sender.provider_message_id = resend_event.provider_message_id
          JOIN orders ON orders.id = sender.order_id
        )
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5('downgrade-resend-' || candidates.id::text)::uuid,
               candidates.order_id,
               candidates.base_sequence + candidates.ordinal::integer,
               'downgrade-resend-' || candidates.id::text,
               'delivery_downgrade',
               candidates.payment_state, candidates.payment_state,
               candidates.fulfillment_state, candidates.fulfillment_state,
               jsonb_build_object(
                 'evidence_kind', 'resend_webhook_event',
                 'webhook_event_id', candidates.id,
                 'svix_event_id', candidates.svix_event_id,
                 'event_type', candidates.event_type,
                 'provider_message_id', candidates.provider_message_id,
                 'payload_sha256', encode(candidates.payload_sha256, 'hex'),
                 'event_created_at', candidates.event_created_at,
                 'processed_at', candidates.processed_at,
                 'migration_revision', '20260827_0002'
               ),
               candidates.created_at
        FROM candidates
        ON CONFLICT (order_id, event_key) DO NOTHING
        """,
        """
        WITH candidates AS (
          SELECT attempt.*,
                 sender.order_id,
                 orders.payment_state,
                 orders.fulfillment_state,
                 COALESCE((
                   SELECT MAX(existing.sequence)
                   FROM state_events existing
                   WHERE existing.order_id = sender.order_id
                 ), 0) AS base_sequence,
                 ROW_NUMBER() OVER (
                   PARTITION BY sender.order_id
                   ORDER BY attempt.created_at, attempt.id
                 ) AS ordinal
          FROM notification_attempts attempt
          JOIN outbox sender ON sender.id = attempt.outbox_id
          JOIN orders ON orders.id = sender.order_id
        )
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5('downgrade-attempt-' || candidates.id::text)::uuid,
               candidates.order_id,
               candidates.base_sequence + candidates.ordinal::integer,
               'downgrade-attempt-' || candidates.id::text,
               'delivery_downgrade',
               candidates.payment_state, candidates.payment_state,
               candidates.fulfillment_state, candidates.fulfillment_state,
               jsonb_strip_nulls(jsonb_build_object(
                 'evidence_kind', 'notification_attempt',
                 'attempt_id', candidates.id,
                 'outbox_id', candidates.outbox_id,
                 'attempt_number', candidates.attempt_number,
                 'worker_id', candidates.worker_id,
                 'started_at', candidates.started_at,
                 'finished_at', candidates.finished_at,
                 'outcome', candidates.outcome,
                 'response_status', candidates.response_status,
                 'provider_message_id', candidates.provider_message_id,
                 'safe_error_code', candidates.safe_error_code,
                 'migration_revision', '20260827_0002'
               )),
               candidates.created_at
        FROM candidates
        ON CONFLICT (order_id, event_key) DO NOTHING
        """,
        """
        WITH candidates AS (
          SELECT sender.*,
                 orders.payment_state,
                 orders.fulfillment_state,
                 COALESCE((
                   SELECT MAX(existing.sequence)
                   FROM state_events existing
                   WHERE existing.order_id = sender.order_id
                 ), 0) AS base_sequence,
                 ROW_NUMBER() OVER (
                   PARTITION BY sender.order_id
                   ORDER BY sender.created_at, sender.id
                 ) AS ordinal
          FROM outbox sender
          JOIN orders ON orders.id = sender.order_id
        )
        INSERT INTO state_events (
          id, order_id, sequence, event_key, source,
          previous_payment_state, payment_state,
          previous_fulfillment_state, fulfillment_state,
          evidence, created_at
        )
        SELECT md5('downgrade-notification-' || candidates.id::text)::uuid,
               candidates.order_id,
               candidates.base_sequence + candidates.ordinal::integer,
               'downgrade-notification-' || candidates.id::text,
               'delivery_downgrade',
               candidates.payment_state, candidates.payment_state,
               candidates.fulfillment_state, candidates.fulfillment_state,
               jsonb_strip_nulls(jsonb_build_object(
                 'evidence_kind', 'notification_outbox',
                 'outbox_id', candidates.id,
                 'message_key', candidates.message_key,
                 'kind', candidates.kind,
                 'state', candidates.state,
                 'attempt_count', candidates.attempt_count,
                 'proof_version', candidates.proof_version,
                 'confirmation_count', candidates.confirmation_count,
                 'confirmation_observation_id', candidates.confirmation_observation_id,
                 'provider_message_id', candidates.provider_message_id,
                 'accepted_at', candidates.accepted_at,
                 'delivered_at', candidates.delivered_at,
                 'safe_error_code', candidates.safe_error_code,
                 'migration_revision', '20260827_0002'
               )),
               candidates.created_at
        FROM candidates
        ON CONFLICT (order_id, event_key) DO NOTHING
        """,
    )


def _delivery_guard_function(mode: str) -> str:
    sender_guard = (
        """
                JOIN resend_webhook_events resend_event
                  ON resend_event.provider_message_id = sender.provider_message_id
                 AND resend_event.event_type = 'email.delivered'
                JOIN bitcoin_confirmation_observations observation
                  ON observation.id = sender.confirmation_observation_id
                 AND observation.order_id = sender.order_id
                 AND observation.proof_version = latest.version
                JOIN bitcoin_confirmation_observations current_observation
                  ON current_observation.order_id = sender.order_id
                 AND current_observation.proof_version = latest.version
                 AND current_observation.id = (
                   SELECT candidate_observation.id
                   FROM bitcoin_confirmation_observations candidate_observation
                   WHERE candidate_observation.order_id = sender.order_id
                     AND candidate_observation.proof_version = latest.version
                   ORDER BY candidate_observation.observed_at DESC,
                            candidate_observation.created_at DESC,
                            candidate_observation.id DESC
                   LIMIT 1
                 )
                WHERE latest.order_id = NEW.id
                  AND latest.version = (
                    SELECT MAX(candidate.version) FROM proof_versions candidate WHERE candidate.order_id = NEW.id
                  )
                  AND sender.message_key =
                    'bitcoin-confirmed-initial-v' || latest.version::text || '-' || NEW.order_reference
                  AND sender.kind = 'bitcoin-confirmed-initial'
                  AND sender.proof_version = latest.version
                  AND sender.confirmation_count >= 1
                  AND observation.observed_confirmations = sender.confirmation_count
                  AND observation.observed_confirmations >= 1
                  AND observation.block_height = verification.block_height
                  AND observation.block_hash = verification.block_hash
                  AND observation.method = verification.method
                  AND observation.confirmation_policy = verification.confirmation_policy
                  AND current_observation.observed_confirmations >= 1
                  AND current_observation.block_height = verification.block_height
                  AND current_observation.block_hash = verification.block_hash
                  AND current_observation.method = verification.method
                  AND current_observation.confirmation_policy = verification.confirmation_policy
                  AND sender.state = 'delivered'
                  AND sender.provider_message_id IS NOT NULL
                  AND sender.accepted_at IS NOT NULL
                  AND sender.delivered_at IS NOT NULL
        """
        if mode == "resend"
        else """
                WHERE latest.order_id = NEW.id
                  AND latest.version = (
                    SELECT MAX(candidate.version) FROM proof_versions candidate WHERE candidate.order_id = NEW.id
                  )
                  AND sender.message_key =
                    'timestamp-complete-v' || latest.version::text || '-' || NEW.order_reference
                  AND sender.kind = 'timestamp-complete'
                  AND sender.state = 'delivered'
                  AND sender.provider_message_id IS NOT NULL
                  AND sender.delivered_at IS NOT NULL
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION enforce_order_state_transition() RETURNS trigger AS $$
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
               {sender_guard}
             ) THEN
            RAISE EXCEPTION 'proof bundle and sender evidence is required';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
