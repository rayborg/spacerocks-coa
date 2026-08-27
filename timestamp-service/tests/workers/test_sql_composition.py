from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.bitcoin.disabled import DisabledVerifier
from app.bitcoin.fixture import FixtureBitcoinVerifier
from app.config.settings import Settings
from app.db.base import Base
from app.db.fulfillment_adapters import create_sql_fulfillment_adapters
from app.db.models import (
    BitcoinConfirmationObservation,
    DurableJob,
    Order,
    ProofBundle,
    ProofVerification,
    ProofVersion,
)
from app.db.models import OutboxMessage as OutboxRecord
from app.db.session import create_session_factory
from app.domain.digest import ManifestDigest
from app.domain.identifiers import OrderReference
from app.jobs.models import JobState
from app.ports.proof import ProofState
from app.proofs.store import make_stored_proof
from app.timestamping.fixture import FixtureTimestamper
from app.worker.composition import PENDING_POLL_MINIMUM_WINDOW, STAMP_JOB, build_worker, create_worker
from app.worker.operator import SqlOperatorCommands, create_operator_commands
from app.worker.runner import Worker

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
ORDER_REFERENCE = OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB")


class Clock:
    def now(self) -> datetime:
        return NOW


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


class Random:
    def bytes(self, length: int) -> bytes:
        return b"x" * length

    def uniform(self, lower: float, upper: float) -> float:
        assert (lower, upper) == (0.0, 1.0)
        return 0.5


class SequenceBitcoinVerifier:
    def __init__(self, confirmations: list[int]) -> None:
        self.confirmations = confirmations
        self.fixture = FixtureBitcoinVerifier()
        self.calls = 0

    async def verify_exact_digest(self, digest, proof_bytes):
        result = await self.fixture.verify_exact_digest(digest, proof_bytes)
        if not result.verified:
            return result
        confirmations = self.confirmations[min(self.calls, len(self.confirmations) - 1)]
        self.calls += 1
        assert result.verified_at is not None
        return replace(
            result,
            confirmations=confirmations,
            verified_at=result.verified_at + timedelta(minutes=self.calls),
        )


@pytest.fixture
def sql_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "test")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        payment_mode="disabled",
        calendar_mode="fixture",
        bitcoin_verifier="fixture",
        database_url=SecretStr("sqlite://"),
        product_version="phase0-test",
    )
    yield factory, settings
    engine.dispose()


def _seed_order(
    factory: sessionmaker[Session],
    *,
    fulfillment_state: str = "queued",
    max_attempts: int = 10,
) -> uuid.UUID:
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        order_reference=ORDER_REFERENCE.value,
        certificate_reference="AZ-2019-0447-HE",
        manifest_digest=bytes.fromhex("de" * 32),
        email="private@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0-test",
        payment_mode="fixture",
        payment_state="paid",
        fulfillment_state=fulfillment_state,
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=NOW,
        checkout_session_id="cs_test_worker",
        payment_intent_id="pi_test_worker",
        fulfillment_key=f"stamp:{order_id}",
        calendar_submitted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    job = DurableJob(
        job_key=order.fulfillment_key,
        order_id=order_id,
        kind=STAMP_JOB,
        state=JobState.AVAILABLE.value,
        attempt_count=0,
        max_attempts=max_attempts,
        available_at=NOW,
        lease_owner=None,
        lease_until=None,
        safe_error_code=None,
        created_at=NOW,
        updated_at=NOW,
    )
    with factory() as session, session.begin():
        session.add_all((order, job))
    return order_id


@pytest.mark.asyncio
async def test_sql_worker_chain_is_durable_exact_and_schema_valid(sql_runtime) -> None:
    factory, settings = sql_runtime
    order_id = _seed_order(factory)
    timestamper = FixtureTimestamper(confirm_on_upgrade=True)
    clock = MutableClock()
    worker = build_worker(
        settings,
        factory,
        worker_id="worker-sql-chain",
        clock=clock,
        random=Random(),
        timestamper=timestamper,
        bitcoin=SequenceBitcoinVerifier([1, 6]),
    )

    assert await worker.run_once()
    assert await worker.run_once()
    assert await worker.run_once()
    assert not await worker.run_once()
    clock.advance(timedelta(minutes=15))
    assert await worker.run_once()
    assert not await worker.run_once()

    adapters = create_sql_fulfillment_adapters(factory)
    proof = await adapters.proofs.latest(ORDER_REFERENCE)
    assert proof is not None
    assert proof.target_digest == ManifestDigest.from_hex("de" * 32)
    assert proof.proof_state == ProofState.BITCOIN_VERIFIED
    assert proof.verification is not None and proof.verification.verified
    assert proof.calendar_submitted_at == datetime(2026, 7, 30, 12, 5, tzinfo=UTC)
    order = await adapters.orders.get_for_fulfillment(str(order_id))
    assert order is not None and order.state.fulfillment.value == "bitcoin_verified"
    assert order.calendar_submitted_at == proof.calendar_submitted_at

    bundle = await adapters.bundles.get(str(order_id), proof.version)
    assert bundle is not None
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        receipt = json.loads(archive.read("timestamp-receipt.json"))
    schema_path = Path(__file__).resolve().parents[3] / "contracts/schemas/timestamp-receipt.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).validate(receipt)
    assert receipt["proof_state"] == "bitcoin_verified"
    assert receipt["calendar_submitted_at"] == "2026-07-30T12:05:00Z"
    assert "private@example.test" not in json.dumps(receipt)

    with factory() as session:
        proof_rows = session.scalars(select(ProofVersion).order_by(ProofVersion.version)).all()
        assert len(proof_rows) == 2
        assert all(row.calendar_submitted_at == proof_rows[0].calendar_submitted_at for row in proof_rows)
        assert session.scalar(select(func.count()).select_from(ProofVerification)) == 1
        assert session.scalar(select(func.count()).select_from(BitcoinConfirmationObservation)) == 2
        assert session.scalar(select(func.count()).select_from(ProofBundle)) == 1
        outbox_rows = session.scalars(select(OutboxRecord)).all()
        assert {row.kind for row in outbox_rows} == {
            "bitcoin-confirmed-initial",
            "bitcoin-confirmed-final",
        }
        assert {row.confirmation_count for row in outbox_rows} == {1, 6}
        jobs = session.scalars(select(DurableJob).order_by(DurableJob.created_at, DurableJob.job_key)).all()
        assert {job.kind for job in jobs} == {
            "stamp_manifest_digest",
            "upgrade_timestamp",
            "deliver_timestamp",
            "monitor_bitcoin_confirmations",
        }
        assert all(job.state == JobState.COMPLETE.value for job in jobs)

    operator = SqlOperatorCommands(settings, factory, adapters)
    await operator.reverify(str(order_id), "request-a")
    await operator.reverify(str(order_id), "request-a")
    await operator.reverify(str(order_id), "request-b")
    with pytest.raises(ValueError, match="reverification_request_id_invalid"):
        await operator.reverify(str(order_id), "invalid request id")
    with factory() as session:
        reverify_jobs = session.scalars(select(DurableJob).where(DurableJob.job_key.like("reverify:%"))).all()
        assert len(reverify_jobs) == 2
        for reverify_job in reverify_jobs:
            reverify_job.available_at = NOW
        session.commit()
    reverify_worker = build_worker(
        settings,
        factory,
        worker_id="worker-reverify-failure",
        clock=Clock(),
        random=Random(),
        timestamper=FixtureTimestamper(),
        bitcoin=DisabledVerifier(),
    )
    assert await reverify_worker.run_once()
    order = await adapters.orders.get_for_fulfillment(str(order_id))
    assert order is not None and order.state.fulfillment.value == "manual_review"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProofVerification)) == 1
        terminal_reverify = session.scalar(
            select(DurableJob).where(
                DurableJob.job_key.like("reverify:%"),
                DurableJob.state == JobState.MANUAL_REVIEW.value,
            )
        )
        assert terminal_reverify is not None and terminal_reverify.state == JobState.MANUAL_REVIEW.value
        terminal_reverify_id = str(terminal_reverify.id)
    with pytest.raises(RuntimeError, match="terminal_recovery_requires_ws1_state_transition"):
        await operator.replay(terminal_reverify_id)


@pytest.mark.asyncio
async def test_sql_stamp_replay_uses_proof_time_without_resubmission(sql_runtime) -> None:
    factory, settings = sql_runtime
    order_id = _seed_order(factory, fulfillment_state="stamping")
    digest = ManifestDigest.from_hex("de" * 32)
    original_timestamper = FixtureTimestamper()
    pending = await original_timestamper.stamp_exact_digest(digest)
    adapters = create_sql_fulfillment_adapters(factory)
    await adapters.proofs.append(
        make_stored_proof(
            ORDER_REFERENCE,
            1,
            digest,
            pending.proof_bytes,
            proof_state=ProofState.CALENDAR_PENDING,
            calendar_submitted_at=pending.calendar_submitted_at,
            verification=None,
        )
    )
    replay_timestamper = FixtureTimestamper()
    worker = build_worker(
        settings,
        factory,
        worker_id="worker-sql-replay",
        clock=Clock(),
        random=Random(),
        timestamper=replay_timestamper,
        bitcoin=FixtureBitcoinVerifier(),
    )

    assert await worker.run_once()
    assert replay_timestamper.stamp_calls == 0
    order = await adapters.orders.get_for_fulfillment(str(order_id))
    assert order is not None
    assert order.state.fulfillment.value == "calendar_pending"
    assert order.calendar_submitted_at == pending.calendar_submitted_at


@pytest.mark.asyncio
async def test_disabled_sql_worker_exhaustion_moves_order_to_manual_review(sql_runtime) -> None:
    factory, settings = sql_runtime
    disabled = settings.model_copy(update={"calendar_mode": "disabled", "bitcoin_verifier": "disabled"})
    order_id = _seed_order(factory, max_attempts=1)
    worker = build_worker(
        disabled,
        factory,
        worker_id="worker-disabled",
        clock=Clock(),
        random=Random(),
    )

    assert await worker.run_once()
    adapters = create_sql_fulfillment_adapters(factory)
    order = await adapters.orders.get_for_fulfillment(str(order_id))
    assert order is not None and order.state.fulfillment.value == "manual_review"
    with factory() as session:
        job = session.scalar(select(DurableJob))
        assert job is not None and job.state == JobState.DEAD_LETTER.value
        job_id = str(job.id)

    operator = SqlOperatorCommands(disabled, factory, adapters)
    with pytest.raises(ValueError, match="terminal_recovery_proof_missing"):
        await operator.replay(job_id)


@pytest.mark.asyncio
async def test_normal_pending_polls_beyond_seven_days_without_retry_or_terminal_state(sql_runtime) -> None:
    factory, settings = sql_runtime
    order_id = _seed_order(factory)
    clock = MutableClock()
    worker = build_worker(
        settings,
        factory,
        worker_id="worker-long-pending",
        clock=clock,
        random=Random(),
        timestamper=FixtureTimestamper(),
        bitcoin=FixtureBitcoinVerifier(),
    )
    assert PENDING_POLL_MINIMUM_WINDOW >= timedelta(days=7)
    assert await worker.run_once()
    assert await worker.run_once()
    for _ in range(28):
        clock.advance(timedelta(hours=6))
        assert await worker.run_once()

    adapters = create_sql_fulfillment_adapters(factory)
    order = await adapters.orders.get_for_fulfillment(str(order_id))
    assert order is not None and order.state.fulfillment.value == "calendar_pending"
    with factory() as session:
        jobs = session.scalars(select(DurableJob)).all()
        assert not any(job.state in {JobState.DEAD_LETTER.value, JobState.MANUAL_REVIEW.value} for job in jobs)
        assert all(job.attempt_count <= 1 for job in jobs)


def test_runtime_factories_exist_and_disabled_default_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("PAYMENT_MODE", "disabled")
    monkeypatch.setenv("CALENDAR_MODE", "disabled")
    monkeypatch.setenv("BITCOIN_VERIFIER", "disabled")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    assert isinstance(create_worker(), Worker)
    assert isinstance(create_operator_commands(), SqlOperatorCommands)
