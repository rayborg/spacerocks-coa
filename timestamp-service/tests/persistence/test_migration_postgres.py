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
        _insert_sender(connection, order_id, order_reference, 1)
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
            _insert_sender(connection, order_id, order_reference, 1)
            connection.execute(
                text("UPDATE orders SET fulfillment_state = 'delivered' WHERE id = :id"),
                {"id": order_id},
            )
        finally:
            transaction.rollback()
