from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Order, OrderToken, ProofBundle, ProofVersion, StateEvent


def make_order(**overrides: object) -> Order:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "order_reference": "ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
        "certificate_reference": "AZ-2019-0447-HE",
        "manifest_digest": b"d" * 32,
        "email": "customer@example.test",
        "amount_minor": 500,
        "currency": "usd",
        "product_version": "phase0",
        "payment_mode": "fixture",
        "payment_state": "checkout_open",
        "fulfillment_state": "awaiting_payment",
        "consent_terms_version": "v1",
        "consent_privacy_version": "v1",
        "consent_accepted_at": now,
        "fulfillment_key": "fulfillment:test",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Order(**values)


def test_schema_stores_token_hash_only_and_has_unique_evidence_keys() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("order_tokens")}
    assert "token_hash" in columns
    assert "token" not in columns and "raw_token" not in columns
    token_uniques = inspect(engine).get_unique_constraints("order_tokens")
    assert any(set(item["column_names"]) == {"order_id", "version"} for item in token_uniques)
    proof_uniques = inspect(engine).get_unique_constraints("proof_versions")
    assert any(set(item["column_names"]) == {"order_id", "version"} for item in proof_uniques)
    state_uniques = inspect(engine).get_unique_constraints("state_events")
    assert any(set(item["column_names"]) == {"order_id", "event_key"} for item in state_uniques)


def test_digest_length_constraint_and_immutable_snapshot_listener() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(make_order(manifest_digest=b"short"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        order = make_order()
        session.add(order)
        session.commit()
        order.email = "changed@example.test"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()


def test_model_metadata_contains_durable_history_tables() -> None:
    expected = {
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
        "notification_attempts",
        "resend_webhook_events",
        "bitcoin_confirmation_observations",
        "task_dispatches",
    }
    assert expected == set(Base.metadata.tables)
    assert OrderToken.__table__.c.token_hash.type.length == 32
    assert ProofVersion.__table__.c.proof_bytes.nullable is False
    assert StateEvent.__table__.c.evidence.nullable is False


def test_proof_state_metadata_constraint_and_bundle_immutability() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    order = make_order()
    with Session(engine) as session:
        session.add(order)
        session.commit()
        session.add(
            ProofVersion(
                order_id=order.id,
                version=1,
                target_digest=order.manifest_digest,
                proof_bytes=b"proof",
                proof_sha256=hashlib.sha256(b"proof").digest(),
                proof_byte_length=5,
                proof_state="calendar_pending",
                calendar_submitted_at=now,
                verification_metadata={"unsafe": True},
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(
            ProofVersion(
                order_id=order.id,
                version=1,
                target_digest=order.manifest_digest,
                proof_bytes=b"proof",
                proof_sha256=hashlib.sha256(b"proof").digest(),
                proof_byte_length=5,
                proof_state="calendar_pending",
                calendar_submitted_at=now,
                verification_metadata=None,
                created_at=now,
            )
        )
        bundle = ProofBundle(
            order_id=order.id,
            proof_version=1,
            bundle_bytes=b"bundle",
            bundle_sha256=hashlib.sha256(b"bundle").digest(),
            bundle_byte_length=6,
            created_at=now,
        )
        session.add(bundle)
        session.commit()
        bundle.bundle_bytes = b"changed"
        with pytest.raises(ValueError, match="append-only"):
            session.commit()


@pytest.mark.parametrize(
    "overrides",
    [
        {"payment_state": "invented"},
        {"fulfillment_state": "invented"},
        {"payment_state": "checkout_open", "fulfillment_state": "queued"},
    ],
)
def test_database_rejects_invalid_or_unpaid_work_states(overrides: dict[str, object]) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(make_order(**overrides))
        with pytest.raises(IntegrityError):
            session.commit()


def test_proof_evidence_foreign_keys_bind_order_and_version_together() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    verification_fks = inspect(engine).get_foreign_keys("proof_verifications")
    bundle_fks = inspect(engine).get_foreign_keys("proof_bundles")
    for foreign_keys in (verification_fks, bundle_fks):
        assert any(
            item["constrained_columns"] == ["order_id", "proof_version"]
            and item["referred_columns"] == ["order_id", "version"]
            for item in foreign_keys
        )


def test_database_rejects_proof_larger_than_parser_boundary() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    order = make_order()
    oversized = b"x" * 262_145
    with Session(engine) as session:
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
                calendar_submitted_at=now,
                verification_metadata=None,
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
