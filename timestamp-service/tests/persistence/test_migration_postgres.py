from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError


def test_offline_postgres_migration_contains_state_and_evidence_guards() -> None:
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
    assert "CREATE FUNCTION enforce_order_state_transition()" in result.stdout
    assert "bitcoin verification evidence is required" in result.stdout
    assert "proof bundle and sender evidence is required" in result.stdout
    assert "MAX(candidate.version)" in result.stdout
    assert "verification.proof_version = latest.version" in result.stdout
    assert "bundle.proof_version = latest.version" in result.stdout
    assert "timestamp-complete-v" in result.stdout
    assert "sender.delivered_at IS NOT NULL" in result.stdout
    assert "proof_byte_length BETWEEN 1 AND 262144" in result.stdout
    assert "octet_length(proof_bytes)" in result.stdout
    assert "FOREIGN KEY(order_id, proof_version) REFERENCES proof_versions (order_id, version)" in result.stdout
    assert "CREATE TABLE task_dispatches" in result.stdout
    assert "timestamp-' || replace(jobs.id::text, '-', '') || '-g1'" in result.stdout
    assert "ON CONFLICT (job_id, generation) DO NOTHING" in result.stdout
    assert "legacy_delivery_without_signed_resend_evidence" in result.stdout
    assert "migration-20260827-legacy-delivery-manual-review" in result.stdout
    assert "SET fulfillment_state = 'manual_review'" in result.stdout
    assert result.stdout.index("migration-20260827-legacy-delivery-manual-review") < result.stdout.index(
        "CREATE OR REPLACE FUNCTION enforce_order_state_transition()"
    )


def test_offline_postgres_downgrade_preserves_evidence_without_inventing_delivery() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql+psycopg://phase0:unused@127.0.0.1:1/phase0"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "20260827_0002:20260730_0001",
            "--sql",
        ],
        cwd=service_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "unsafe delivery downgrade: unmatched Resend webhook evidence cannot be represented" in result.stdout
    assert "downgrade-confirmation-" in result.stdout
    assert "downgrade-resend-" in result.stdout
    assert "downgrade-attempt-" in result.stdout
    assert "downgrade-notification-" in result.stdout
    assert "NULL-legacy" not in result.stdout
    assert "NULL-downgrade" not in result.stdout
    assert "downgrade_final_notice_unrepresentable" in result.stdout
    assert "downgrade_delivery_evidence_missing" in result.stdout
    assert "sender.state = 'delivered'" in result.stdout
    assert "resend_event.event_type = 'email.delivered'" in result.stdout
    assert "COALESCE(sender.delivered_at, sender.accepted_at)" not in result.stdout
    assert "WHEN sender.provider_message_id IS NOT NULL THEN 'delivered'" not in result.stdout


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured")
def test_baseline_migration_upgrades_postgres() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment["TEST_POSTGRES_URL"]
    subprocess.run(["alembic", "upgrade", "head"], cwd=service_root, env=environment, check=True)
    inspector = inspect(create_engine(environment["TEST_POSTGRES_URL"]))
    assert {
        "orders",
        "order_tokens",
        "idempotency_requests",
        "rate_counters",
        "stripe_events",
        "durable_jobs",
        "job_attempts",
        "proof_versions",
        "proof_verifications",
        "proof_bundles",
        "state_events",
        "outbox",
        "task_dispatches",
    }.issubset(inspector.get_table_names())


def _insert_order(connection: Connection, order_id: uuid.UUID, reference: str, state: str) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO orders (
              id, order_reference, certificate_reference, manifest_digest, email,
              amount_minor, currency, product_version, payment_mode, payment_state,
              fulfillment_state, consent_terms_version, consent_privacy_version,
              consent_accepted_at, fulfillment_key, calendar_submitted_at, created_at, updated_at
            ) VALUES (
              :id, :reference, 'TEST-001', :digest, 'customer@example.test',
              500, 'usd', 'phase0', 'fixture', 'paid', :state, 'v1', 'v1',
              :now, :fulfillment_key, :now, :now, :now
            )
            """
        ),
        {
            "id": order_id,
            "reference": reference,
            "digest": b"d" * 32,
            "state": state,
            "now": now,
            "fulfillment_key": f"stamp:{order_id}",
        },
    )


def _insert_proof(connection: Connection, order_id: uuid.UUID, version: int) -> None:
    now = datetime.now(UTC)
    proof_bytes = bytes([version])
    connection.execute(
        text(
            """
            INSERT INTO proof_versions (
              id, order_id, version, target_digest, proof_bytes, proof_sha256,
              proof_byte_length, proof_state, calendar_submitted_at,
              verification_metadata, created_at
            ) VALUES (
              :id, :order_id, :version, :digest, :proof_bytes, :proof_sha256,
              1, 'calendar_pending', :now, NULL, :now
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "order_id": order_id,
            "version": version,
            "digest": b"d" * 32,
            "proof_bytes": proof_bytes,
            "proof_sha256": bytes([version]) * 32,
            "now": now,
        },
    )


def _insert_verification(connection: Connection, order_id: uuid.UUID, version: int) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO proof_verifications (
              id, order_id, proof_version, method, verified_at, block_height,
              block_hash, block_time, confirmation_policy, created_at
            ) VALUES (
              :id, :order_id, :version, 'fixture-exact-digest', :now, 900000,
              :block_hash, :now, 'fixture-confirmed', :now
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "order_id": order_id,
            "version": version,
            "now": now,
            "block_hash": "ab" * 32,
        },
    )


def _insert_bundle(connection: Connection, order_id: uuid.UUID, version: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO proof_bundles (
              id, order_id, proof_version, bundle_bytes, bundle_sha256,
              bundle_byte_length, created_at
            ) VALUES (:id, :order_id, :version, :bundle, :sha256, 6, :now)
            """
        ),
        {
            "id": uuid.uuid4(),
            "order_id": order_id,
            "version": version,
            "bundle": b"bundle",
            "sha256": b"b" * 32,
            "now": datetime.now(UTC),
        },
    )


def _insert_sender(
    connection: Connection,
    order_id: uuid.UUID,
    order_reference: str,
    version: int,
) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO outbox (
              id, message_key, order_id, kind, recipient, payload, state,
              attempt_count, available_at, provider_message_id, delivered_at, created_at
            ) VALUES (
              :id, :message_key, :order_id, 'timestamp-complete', 'customer@example.test',
              CAST(:payload AS jsonb), 'delivered', 1, :now, :provider_message_id, :now, :now
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "message_key": f"timestamp-complete-v{version}-{order_reference}",
            "order_id": order_id,
            "payload": json.dumps({"order_reference": order_reference}),
            "now": now,
            "provider_message_id": f"provider-{uuid.uuid4()}",
        },
    )


def _insert_current_sender(
    connection: Connection,
    order_id: uuid.UUID,
    order_reference: str,
    version: int,
    *,
    kind: str = "bitcoin-confirmed-initial",
    state: str = "delivered",
    confirmations: int = 1,
    event_type: str | None = "email.delivered",
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    observation_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    provider_message_id = f"provider-{uuid.uuid4()}"
    connection.execute(
        text(
            """
            INSERT INTO bitcoin_confirmation_observations (
              id, order_id, proof_version, observed_confirmations, block_height,
              block_hash, method, confirmation_policy, observed_at, event_key, created_at
            ) VALUES (
              :id, :order_id, :version, :confirmations, 900000,
              :block_hash, 'fixture-exact-digest', 'fixture-confirmed', :now, :event_key, :now
            )
            """
        ),
        {
            "id": observation_id,
            "order_id": order_id,
            "version": version,
            "confirmations": confirmations,
            "block_hash": "ab" * 32,
            "now": now,
            "event_key": f"runtime-confirmations-{confirmations}-{outbox_id}",
        },
    )
    message_key = f"{kind}-v{version}-{order_reference}"
    connection.execute(
        text(
            """
            INSERT INTO outbox (
              id, message_key, order_id, kind, recipient, payload, state,
              attempt_count, max_attempts, available_at, proof_version,
              confirmation_count, confirmation_observation_id,
              provider_message_id, accepted_at, delivered_at,
              idempotency_expires_at, created_at, updated_at
            ) VALUES (
              :id, :message_key, :order_id, :kind, 'customer@example.test',
              CAST(:payload AS jsonb), :state, 1, 12, :now, :version,
              :confirmations, :observation_id, :provider_message_id, :now, :delivered_at,
              :expires_at, :now, :now
            )
            """
        ),
        {
            "id": outbox_id,
            "message_key": message_key,
            "order_id": order_id,
            "kind": kind,
            "payload": json.dumps({"template": kind, "order_reference": order_reference}),
            "state": state,
            "now": now,
            "version": version,
            "confirmations": confirmations,
            "observation_id": observation_id,
            "provider_message_id": provider_message_id,
            "delivered_at": now if state == "delivered" else None,
            "expires_at": now,
        },
    )
    if event_type is not None:
        connection.execute(
            text(
                """
                INSERT INTO resend_webhook_events (
                  id, svix_event_id, event_type, provider_message_id, payload_sha256,
                  event_created_at, processed_at, created_at
                ) VALUES (
                  :id, :svix_event_id, :event_type, :provider_message_id, :payload_sha256,
                  :now, :now, :now
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "svix_event_id": f"msg_{uuid.uuid4().hex}",
                "event_type": event_type,
                "provider_message_id": provider_message_id,
                "payload_sha256": b"w" * 32,
                "now": now,
            },
        )
    return provider_message_id, now


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured")
def test_latest_version_evidence_trigger_rejects_stale_evidence_and_accepts_worker_ordering() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment["TEST_POSTGRES_URL"]
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=service_root, env=environment, check=True)
    engine = create_engine(environment["TEST_POSTGRES_URL"])

    connection = engine.connect()
    transaction = connection.begin()
    try:
        order_id = uuid.uuid4()
        _insert_order(connection, order_id, f"ts_{'1' * 25}A", "calendar_pending")
        _insert_proof(connection, order_id, 1)
        _insert_verification(connection, order_id, 1)
        _insert_proof(connection, order_id, 2)
        with pytest.raises(DBAPIError, match="bitcoin verification evidence is required"):
            connection.execute(
                text("UPDATE orders SET fulfillment_state = 'bitcoin_verified' WHERE id = :id"),
                {"id": order_id},
            )
    finally:
        transaction.rollback()
        connection.close()

    connection = engine.connect()
    transaction = connection.begin()
    try:
        order_id = uuid.uuid4()
        order_reference = f"ts_{'2' * 25}B"
        _insert_order(connection, order_id, order_reference, "bitcoin_verified")
        _insert_proof(connection, order_id, 1)
        _insert_verification(connection, order_id, 1)
        _insert_bundle(connection, order_id, 1)
        _insert_current_sender(connection, order_id, order_reference, 1)
        _insert_proof(connection, order_id, 2)
        with pytest.raises(DBAPIError, match="proof bundle and sender evidence is required"):
            connection.execute(
                text("UPDATE orders SET fulfillment_state = 'delivered' WHERE id = :id"),
                {"id": order_id},
            )
    finally:
        transaction.rollback()
        connection.close()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            order_id = uuid.uuid4()
            order_reference = f"ts_{'3' * 25}C"
            _insert_order(connection, order_id, order_reference, "calendar_pending")
            _insert_proof(connection, order_id, 1)
            _insert_verification(connection, order_id, 1)
            connection.execute(
                text("UPDATE orders SET fulfillment_state = 'bitcoin_verified' WHERE id = :id"),
                {"id": order_id},
            )
            _insert_bundle(connection, order_id, 1)
            _insert_current_sender(connection, order_id, order_reference, 1)
            connection.execute(
                text("UPDATE orders SET fulfillment_state = 'delivered' WHERE id = :id"),
                {"id": order_id},
            )
        finally:
            transaction.rollback()


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured")
def test_delivery_upgrade_and_downgrade_preserve_semantics_and_evidence() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment["TEST_POSTGRES_URL"]
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=service_root,
        env=environment,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260730_0001"],
        cwd=service_root,
        env=environment,
        check=True,
    )
    engine = create_engine(environment["TEST_POSTGRES_URL"])

    legacy_order_id = uuid.uuid4()
    legacy_reference = f"ts_{'4' * 25}D"
    with engine.begin() as connection:
        _insert_order(connection, legacy_order_id, legacy_reference, "delivered")
        _insert_proof(connection, legacy_order_id, 1)
        _insert_verification(connection, legacy_order_id, 1)
        _insert_bundle(connection, legacy_order_id, 1)
        _insert_sender(connection, legacy_order_id, legacy_reference, 1)

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=service_root, env=environment, check=True)
    with engine.connect() as connection:
        legacy_state = connection.execute(
            text("SELECT fulfillment_state FROM orders WHERE id = :id"),
            {"id": legacy_order_id},
        ).scalar_one()
        legacy_sender = connection.execute(
            text(
                """
                SELECT kind, state, accepted_at, delivered_at, safe_error_code
                FROM outbox WHERE order_id = :id
                """
            ),
            {"id": legacy_order_id},
        ).one()
        migration_event = connection.execute(
            text(
                """
                SELECT previous_fulfillment_state, fulfillment_state, evidence
                FROM state_events
                WHERE order_id = :id
                  AND event_key = 'migration-20260827-legacy-delivery-manual-review'
                """
            ),
            {"id": legacy_order_id},
        ).one()
    assert legacy_state == "manual_review"
    assert legacy_sender.kind == "bitcoin-confirmed-initial"
    assert legacy_sender.state == "accepted"
    assert legacy_sender.accepted_at is not None and legacy_sender.delivered_at is None
    assert legacy_sender.safe_error_code == "legacy_delivery_manual_review"
    assert migration_event.previous_fulfillment_state == "delivered"
    assert migration_event.fulfillment_state == "manual_review"
    assert migration_event.evidence["reason"] == "legacy_delivery_without_signed_resend_evidence"

    scenarios = {
        "accepted": (f"ts_{'5' * 25}E", "bitcoin-confirmed-initial", "accepted", 1, None),
        "failed": (f"ts_{'6' * 25}F", "bitcoin-confirmed-initial", "failed", 1, "email.failed"),
        "bounced": (f"ts_{'7' * 25}G", "bitcoin-confirmed-initial", "failed", 1, "email.bounced"),
        "complained": (f"ts_{'8' * 25}H", "bitcoin-confirmed-initial", "failed", 1, "email.complained"),
        "final": (f"ts_{'9' * 25}J", "bitcoin-confirmed-final", "delivered", 6, "email.delivered"),
        "genuine": (f"ts_{'A' * 25}K", "bitcoin-confirmed-initial", "delivered", 1, "email.delivered"),
    }
    scenario_ids: dict[str, uuid.UUID] = {}
    genuine_delivered_at: datetime | None = None
    with engine.begin() as connection:
        for name, (reference, kind, state, confirmations, event_type) in scenarios.items():
            order_id = uuid.uuid4()
            scenario_ids[name] = order_id
            _insert_order(connection, order_id, reference, "bitcoin_verified")
            _insert_proof(connection, order_id, 1)
            _insert_verification(connection, order_id, 1)
            _insert_bundle(connection, order_id, 1)
            _provider_id, delivered_at = _insert_current_sender(
                connection,
                order_id,
                reference,
                1,
                kind=kind,
                state=state,
                confirmations=confirmations,
                event_type=event_type,
            )
            if name == "genuine":
                genuine_delivered_at = delivered_at
                connection.execute(
                    text("UPDATE orders SET fulfillment_state = 'delivered' WHERE id = :id"),
                    {"id": order_id},
                )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "20260730_0001"],
        cwd=service_root,
        env=environment,
        check=True,
    )
    with engine.connect() as connection:
        downgraded = {
            name: connection.execute(
                text("SELECT kind, state, delivered_at FROM outbox WHERE order_id = :id"),
                {"id": order_id},
            ).one()
            for name, order_id in scenario_ids.items()
        }
        order_states = {
            name: connection.execute(
                text("SELECT fulfillment_state FROM orders WHERE id = :id"),
                {"id": order_id},
            ).scalar_one()
            for name, order_id in scenario_ids.items()
        }
        archived_kinds = set(
            connection.execute(
                text(
                    """
                    SELECT evidence->>'evidence_kind'
                    FROM state_events
                    WHERE source = 'delivery_downgrade'
                    """
                )
            ).scalars()
        )
    assert downgraded["accepted"].kind == "timestamp-complete"
    assert downgraded["accepted"].state == "accepted" and downgraded["accepted"].delivered_at is None
    for name in ("failed", "bounced", "complained"):
        assert downgraded[name].kind == "timestamp-complete"
        assert downgraded[name].state == "failed" and downgraded[name].delivered_at is None
    assert downgraded["final"].kind == "bitcoin-confirmed-final"
    assert downgraded["final"].state == "dead_letter" and downgraded["final"].delivered_at is None
    assert downgraded["genuine"].kind == "timestamp-complete"
    assert downgraded["genuine"].state == "delivered"
    assert downgraded["genuine"].delivered_at == genuine_delivered_at
    assert order_states["genuine"] == "delivered"
    assert all(order_states[name] == "bitcoin_verified" for name in scenarios if name != "genuine")
    assert {
        "bitcoin_confirmation_observation",
        "resend_webhook_event",
        "notification_outbox",
    }.issubset(archived_kinds)
    inspector = inspect(engine)
    assert "resend_webhook_events" not in inspector.get_table_names()
    assert "bitcoin_confirmation_observations" not in inspector.get_table_names()
    engine.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=service_root, env=environment, check=True)
