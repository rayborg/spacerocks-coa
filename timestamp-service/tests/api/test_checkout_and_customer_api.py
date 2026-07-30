from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conftest import REPOSITORY_ROOT, ServiceContext
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import event, func, select

from app.db.models import (
    IdempotencyRequest,
    Order,
    OrderToken,
    OutboxMessage,
    ProofBundle,
    ProofVerification,
    ProofVersion,
    RateCounter,
    StateEvent,
)
from app.main import create_app
from app.observability.logging import SafeJsonFormatter
from app.payments.models import HostedCheckoutRequest, HostedCheckoutResult
from app.ports.proof import ProofBundleContext, StoredProof


def checkout_payload() -> dict[str, Any]:
    path = REPOSITORY_ROOT / "contracts/fixtures/checkout-request.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def idempotency_key(seed: bytes = b"checkout-key-001") -> str:
    return base64.urlsafe_b64encode(seed.ljust(16, b"0")[:16]).rstrip(b"=").decode("ascii")


def create_checkout(client: TestClient, key: str | None = None) -> dict[str, Any]:
    response = client.post(
        "/v1/checkout",
        json=checkout_payload(),
        headers={"Idempotency-Key": key or idempotency_key()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_checkout_matches_frozen_contract_and_persists_no_raw_token(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        response = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
    assert response.status_code == 201
    schema = json.loads(
        (REPOSITORY_ROOT / "contracts/schemas/checkout-response.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(response.json())
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    raw_token = response.json()["status_token"]
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        token = session.scalar(select(OrderToken))
        assert token is not None
        assert raw_token.encode() != token.token_hash
        assert len(token.token_hash) == 32
        assert token.pepper_version == 1


def test_idempotent_replay_rotates_token_without_recreating_checkout(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        first = create_checkout(client)
        in_progress = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
        first_status = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {first['status_token']}"},
        )
        with context.session_factory() as session, session.begin():
            reservation = session.scalar(select(IdempotencyRequest))
            assert reservation is not None
            reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        second = create_checkout(client)
        assert first["order_reference"] == second["order_reference"]
        assert first["checkout_url"] == second["checkout_url"]
        assert first["status_token"] != second["status_token"]
        old_status = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {first['status_token']}"},
        )
        new_status = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {second['status_token']}"},
        )
    assert in_progress.status_code == 425
    assert "status_token" not in in_progress.text
    assert first_status.status_code == 200
    assert old_status.status_code == 401
    assert new_status.status_code == 200
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(OrderToken)) == 2
        revoked_count = session.scalar(
            select(func.count()).select_from(OrderToken).where(OrderToken.revoked_at.is_not(None))
        )
        assert revoked_count == 1


def test_idempotency_key_body_mismatch_is_conflict(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    payload = checkout_payload()
    with TestClient(context.app) as client:
        assert client.post(
            "/v1/checkout", json=payload, headers={"Idempotency-Key": idempotency_key()}
        ).status_code == 201
        payload["certificate_reference"] = "OTHER-001"
        response = client.post(
            "/v1/checkout", json=payload, headers={"Idempotency-Key": idempotency_key()}
        )
    assert response.status_code == 409
    assert "OTHER-001" not in response.text


def test_provider_failure_leaves_retryable_reservation_without_token(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    original_checkout = context.provider.create_checkout
    captured_order_ids: list[str] = []

    async def unsafe_checkout(request: HostedCheckoutRequest, _idempotency_key: str) -> HostedCheckoutResult:
        captured_order_ids.append(request.internal_order_id)
        return HostedCheckoutResult("cs_test_unsafe", "https://evil.example/steal")

    context.provider.create_checkout = unsafe_checkout  # type: ignore[method-assign]
    with TestClient(context.app) as client:
        response = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
    assert response.status_code == 503
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        reservation = session.scalar(select(IdempotencyRequest))
        assert order is not None
        assert reservation is not None and reservation.checkout_state == "processing"
        assert captured_order_ids == [str(order.id)]
        assert session.scalar(select(func.count()).select_from(OrderToken)) == 0

    context.provider.create_checkout = original_checkout  # type: ignore[method-assign]
    with TestClient(context.app) as client:
        in_progress = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
        with context.session_factory() as session, session.begin():
            reservation = session.scalar(select(IdempotencyRequest))
            assert reservation is not None
            reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        retry = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
    assert in_progress.status_code == 425
    assert retry.status_code == 201
    with context.session_factory() as session:
        reservation = session.scalar(select(IdempotencyRequest))
        assert reservation is not None and reservation.checkout_state == "completed"
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(OrderToken)) == 1


def test_provider_is_called_only_after_reservation_commit(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    original_checkout = context.provider.create_checkout

    async def assert_committed(request: HostedCheckoutRequest, key: str) -> HostedCheckoutResult:
        with context.session_factory() as session:
            reservation = session.scalar(select(IdempotencyRequest))
            order = session.scalar(select(Order))
            assert reservation is not None and reservation.checkout_state == "processing"
            assert order is not None and str(order.id) == request.internal_order_id
        return await original_checkout(request, key)

    context.provider.create_checkout = assert_committed  # type: ignore[method-assign]
    with TestClient(context.app) as client:
        response = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
    assert response.status_code == 201


def test_checkout_inserts_order_before_immediate_fk_reservation(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    statements: list[str] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    event.listen(context.engine, "before_cursor_execute", capture_sql)
    try:
        with TestClient(context.app) as client:
            response = client.post(
                "/v1/checkout",
                json=checkout_payload(),
                headers={"Idempotency-Key": idempotency_key()},
            )
    finally:
        event.remove(context.engine, "before_cursor_execute", capture_sql)
    assert response.status_code == 201
    order_insert = next(index for index, sql in enumerate(statements) if "insert into orders" in sql)
    reservation_insert = next(
        index for index, sql in enumerate(statements) if "insert into idempotency_requests" in sql
    )
    assert order_insert < reservation_insert


def test_concurrent_checkout_reuses_one_reservation_and_provider_key(app_factory: Any, tmp_path: Path) -> None:
    context: ServiceContext = app_factory(sqlite_url=f"sqlite:///{tmp_path / 'checkout.db'}")
    original_checkout = context.provider.create_checkout
    provider_keys: list[str] = []

    async def capture_key(request: HostedCheckoutRequest, key: str) -> HostedCheckoutResult:
        provider_keys.append(key)
        return await original_checkout(request, key)

    context.provider.create_checkout = capture_key  # type: ignore[method-assign]

    def submit() -> tuple[int, dict[str, Any]]:
        with TestClient(context.app) as client:
            response = client.post(
                "/v1/checkout",
                json=checkout_payload(),
                headers={"Idempotency-Key": idempotency_key()},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: submit(), range(2)))
    assert sorted(status for status, _body in responses) == [201, 425]
    success = next(body for status, body in responses if status == 201)
    blocked = next(body for status, body in responses if status == 425)
    assert "status_token" not in blocked
    assert len(set(provider_keys)) == 1
    with TestClient(context.app) as client:
        active = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {success['status_token']}"},
        )
        with context.session_factory() as session, session.begin():
            reservation = session.scalar(select(IdempotencyRequest))
            assert reservation is not None
            reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        recovered = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={"Idempotency-Key": idempotency_key()},
        )
        old_after_recovery = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {success['status_token']}"},
        )
        new_after_recovery = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {recovered.json()['status_token']}"},
        )
    assert active.status_code == 200
    assert recovered.status_code == 201
    assert old_after_recovery.status_code == 401
    assert new_after_recovery.status_code == 200
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRequest)) == 1


def test_expired_processing_lease_allows_only_new_finalizer_to_return_token(
    app_factory: Any,
    tmp_path: Path,
) -> None:
    context: ServiceContext = app_factory(sqlite_url=f"sqlite:///{tmp_path / 'lease-race.db'}")
    original_checkout = context.provider.create_checkout
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    async def delayed_first(request: HostedCheckoutRequest, key: str) -> HostedCheckoutResult:
        nonlocal calls
        result = await original_checkout(request, key)
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        return result

    context.provider.create_checkout = delayed_first  # type: ignore[method-assign]

    def submit() -> tuple[int, dict[str, Any]]:
        with TestClient(context.app) as client:
            response = client.post(
                "/v1/checkout",
                json=checkout_payload(),
                headers={"Idempotency-Key": idempotency_key()},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        stale_future = pool.submit(submit)
        assert first_started.wait(timeout=5)
        with context.session_factory() as session, session.begin():
            reservation = session.scalar(select(IdempotencyRequest))
            assert reservation is not None
            reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        current = submit()
        release_first.set()
        stale = stale_future.result(timeout=5)
    assert current[0] == 201
    assert stale[0] == 425
    assert "status_token" not in stale[1]
    with TestClient(context.app) as client:
        assert client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {current[1]['status_token']}"},
        ).status_code == 200
    with context.session_factory() as session:
        active_tokens = session.scalar(
            select(func.count()).select_from(OrderToken).where(OrderToken.revoked_at.is_(None))
        )
        assert active_tokens == 1


def test_status_is_safe_rotatable_and_contract_valid(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        authorization = {"Authorization": f"Bearer {checkout['status_token']}"}
        status_response = client.get("/v1/orders/status", headers=authorization)
        rotation = client.post("/v1/orders/rotate-token", headers=authorization)
        rejected = client.get("/v1/orders/status", headers=authorization)
        replacement = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {rotation.json()['status_token']}"},
        )
    assert status_response.status_code == 200
    body = status_response.json()
    assert "email" not in body
    assert "checkout_session_id" not in body
    assert "payment_intent_id" not in body
    schema = json.loads((REPOSITORY_ROOT / "contracts/schemas/order-status.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(body)
    assert rotation.status_code == 200
    rotation_schema = json.loads(
        (REPOSITORY_ROOT / "contracts/schemas/rotate-token-response.schema.json").read_text()
    )
    Draft202012Validator(rotation_schema).validate(rotation.json())
    assert rejected.status_code == 401
    assert replacement.status_code == 200


def test_stamping_status_suppresses_crash_left_proof_metadata(app_factory: Any) -> None:
    bundler = CapturingBundler()
    context: ServiceContext = app_factory(proof_bundler=bundler)
    now = datetime.now(UTC)
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "stamping"
            order.calendar_submitted_at = now
            proof_bytes = b"crash-left-pending-proof"
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=proof_bytes,
                    proof_sha256=hashlib.sha256(proof_bytes).digest(),
                    proof_byte_length=len(proof_bytes),
                    proof_state="calendar_pending",
                    calendar_submitted_at=now,
                    verification_metadata=None,
                    created_at=now,
                )
            )
        headers = {"Authorization": f"Bearer {checkout['status_token']}"}
        stamping = client.get("/v1/orders/status", headers=headers)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.fulfillment_state = "calendar_pending"
        pending = client.get("/v1/orders/status", headers=headers)
    assert stamping.status_code == 200
    assert stamping.json()["proof_available"] is False
    assert "calendar_submitted_at" not in stamping.json()
    assert "bitcoin_verified_at" not in stamping.json()
    assert pending.status_code == 200
    assert pending.json()["proof_available"] is True
    assert pending.json()["calendar_submitted_at"]
    assert "bitcoin_verified_at" not in pending.json()


def test_verified_status_requires_bundle_for_current_latest_version(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    now = datetime.now(UTC)
    old_bundle = b"PK\x03\x04old-version-bundle"
    current_bundle = b"PK\x03\x04current-version-bundle"
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "bitcoin_verified"
            order.calendar_submitted_at = now
            for version in (1, 2):
                proof_bytes = f"verified-proof-{version}".encode()
                session.add(
                    ProofVersion(
                        order_id=order.id,
                        version=version,
                        target_digest=order.manifest_digest,
                        proof_bytes=proof_bytes,
                        proof_sha256=hashlib.sha256(proof_bytes).digest(),
                        proof_byte_length=len(proof_bytes),
                        proof_state="calendar_pending",
                        calendar_submitted_at=now,
                        verification_metadata=None,
                        created_at=now,
                    )
                )
                session.flush()
                session.add(
                    ProofVerification(
                        order_id=order.id,
                        proof_version=version,
                        method="fixture-exact-digest",
                        verified_at=now,
                        block_height=900000 + version,
                        block_hash="ab" * 32,
                        block_time=now,
                        confirmation_policy="fixture-confirmed",
                        created_at=now,
                    )
                )
            session.add(
                ProofBundle(
                    order_id=order.id,
                    proof_version=1,
                    bundle_bytes=old_bundle,
                    bundle_sha256=hashlib.sha256(old_bundle).digest(),
                    bundle_byte_length=len(old_bundle),
                    created_at=now,
                )
            )
        headers = {"Authorization": f"Bearer {checkout['status_token']}"}
        without_current_bundle = client.get("/v1/orders/status", headers=headers)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            session.add(
                ProofBundle(
                    order_id=order.id,
                    proof_version=2,
                    bundle_bytes=current_bundle,
                    bundle_sha256=hashlib.sha256(current_bundle).digest(),
                    bundle_byte_length=len(current_bundle),
                    created_at=now,
                )
            )
        with_current_bundle = client.get("/v1/orders/status", headers=headers)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.fulfillment_state = "delivered"
        delivered_without_sender = client.get("/v1/orders/status", headers=headers)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            session.add(
                OutboxMessage(
                    message_key=f"timestamp-complete-v2-{order.order_reference}",
                    order_id=order.id,
                    kind="timestamp-complete",
                    recipient=order.email,
                    payload={"order_reference": order.order_reference},
                    state="delivered",
                    attempt_count=1,
                    available_at=now,
                    lease_owner=None,
                    lease_until=None,
                    provider_message_id="fixture-message-current-v2",
                    delivered_at=now,
                    safe_error_code=None,
                    created_at=now,
                )
            )
        delivered = client.get("/v1/orders/status", headers=headers)
    assert without_current_bundle.status_code == 200
    assert without_current_bundle.json()["proof_available"] is False
    assert without_current_bundle.json()["calendar_submitted_at"]
    assert without_current_bundle.json()["bitcoin_verified_at"]
    assert with_current_bundle.status_code == 200
    assert with_current_bundle.json()["proof_available"] is True
    assert delivered_without_sender.status_code == 503
    assert delivered.status_code == 200
    assert delivered.json()["proof_available"] is True


def test_strict_bearer_auth_and_pending_proof_are_generic(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        token = checkout["status_token"]
        responses = [
            client.get("/v1/orders/status"),
            client.get("/v1/orders/status", headers={"Authorization": token}),
            client.get("/v1/orders/status", headers={"Authorization": f"bearer {token}"}),
            client.get("/v1/orders/status", headers={"Authorization": "Bearer invalid"}),
        ]
        proof = client.get("/v1/orders/proof", headers={"Authorization": f"Bearer {token}"})
    assert {response.status_code for response in responses} == {401}
    assert len({response.text for response in responses}) == 1
    assert proof.status_code == 425


def test_expired_token_and_browser_redirect_cannot_authorize(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            token = session.scalar(select(OrderToken))
            assert token is not None
            token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired = client.get(
            "/v1/orders/status",
            headers={"Authorization": f"Bearer {checkout['status_token']}"},
        )
        redirect = client.get(
            "/timestamp/status",
            params={"session_id": "cs_test_attacker_controlled"},
        )
    assert expired.status_code == 401
    assert redirect.status_code == 404
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None
        assert order.payment_state == "checkout_open"
        assert order.fulfillment_state == "awaiting_payment"


class CapturingBundler:
    def __init__(self) -> None:
        self.receipt: bytes | None = None

    async def build(
        self,
        proof: StoredProof,
        receipt_json: bytes,
        context: ProofBundleContext,
    ) -> bytes:
        assert len(proof.target_digest.value) == 32
        assert context.certificate_reference.value == "AZ-2019-0447-HE"
        assert context.service_version == "phase0-test-version"
        self.receipt = receipt_json
        return b"PK\x03\x04fixture"


class ManualReviewRaceBundler:
    def __init__(self) -> None:
        self.session_factory: Any = None

    async def build(
        self,
        _proof: StoredProof,
        _receipt_json: bytes,
        _context: ProofBundleContext,
    ) -> bytes:
        with self.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.fulfillment_state = "manual_review"
        return b"PK\x03\x04must-not-be-returned"


def test_proof_endpoint_delegates_bundle_without_implementing_zip(app_factory: Any) -> None:
    bundler = CapturingBundler()
    context: ServiceContext = app_factory(proof_bundler=bundler, product_version="phase0-test-version")
    now = datetime.now(UTC)
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "calendar_pending"
            order.calendar_submitted_at = now
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
        response = client.get(
            "/v1/orders/proof",
            headers={
                "Authorization": f"Bearer {checkout['status_token']}",
                "Origin": "https://coa.example.test",
            },
        )
    assert response.status_code == 200
    assert response.content == b"PK\x03\x04fixture"
    assert response.headers["content-disposition"].startswith('attachment; filename="ts_')
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-expose-headers"] == (
        "Content-Disposition, Content-Length, Cache-Control"
    )
    assert bundler.receipt is not None
    receipt = json.loads(bundler.receipt)
    schema = json.loads((REPOSITORY_ROOT / "contracts/schemas/timestamp-receipt.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
    assert receipt["service_version"] == "phase0-test-version"
    with context.session_factory() as session:
        audit = session.scalar(select(StateEvent).where(StateEvent.source == "customer_download"))
        assert audit is not None
        assert audit.evidence["artifact_kind"] == "generated_pending"
        assert audit.evidence["artifact_sha256"] == hashlib.sha256(response.content).hexdigest()


def test_pending_download_rechecks_manual_review_after_bundle_race(app_factory: Any) -> None:
    bundler = ManualReviewRaceBundler()
    context: ServiceContext = app_factory(proof_bundler=bundler)
    bundler.session_factory = context.session_factory
    now = datetime.now(UTC)
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "calendar_pending"
            order.calendar_submitted_at = now
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"race-proof",
                    proof_sha256=hashlib.sha256(b"race-proof").digest(),
                    proof_byte_length=len(b"race-proof"),
                    proof_state="calendar_pending",
                    calendar_submitted_at=now,
                    verification_metadata=None,
                    created_at=now,
                )
            )
        response = client.get(
            "/v1/orders/proof",
            headers={"Authorization": f"Bearer {checkout['status_token']}"},
        )
    assert response.status_code == 409
    assert b"must-not-be-returned" not in response.content
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.fulfillment_state == "manual_review"
        assert session.scalar(
            select(func.count()).select_from(StateEvent).where(StateEvent.source == "customer_download")
        ) == 0


def test_verified_download_returns_exact_persisted_bytes_and_records_each_audit(app_factory: Any) -> None:
    bundler = CapturingBundler()
    context: ServiceContext = app_factory(proof_bundler=bundler)
    now = datetime.now(UTC)
    durable_bundle = b"PK\x03\x04persisted-verified-bundle"
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "delivered"
            order.calendar_submitted_at = now
            metadata = {
                "bitcoin": {
                    "block_height": 900000,
                    "block_hash": "ab" * 32,
                    "block_time": now.isoformat(),
                    "confirmation_policy": "fixture-confirmed",
                },
                "verification_method": "fixture-exact-digest",
                "verified_at": now.isoformat(),
            }
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"verified-proof",
                    proof_sha256=hashlib.sha256(b"verified-proof").digest(),
                    proof_byte_length=len(b"verified-proof"),
                    proof_state="bitcoin_verified",
                    calendar_submitted_at=now,
                    verification_metadata=metadata,
                    created_at=now,
                )
            )
            session.flush()
            session.add_all(
                [
                    ProofVerification(
                        order_id=order.id,
                        proof_version=1,
                        method="fixture-exact-digest",
                        verified_at=now,
                        block_height=900000,
                        block_hash="ab" * 32,
                        block_time=now,
                        confirmation_policy="fixture-confirmed",
                        created_at=now,
                    ),
                    ProofBundle(
                        order_id=order.id,
                        proof_version=1,
                        bundle_bytes=durable_bundle,
                        bundle_sha256=hashlib.sha256(durable_bundle).digest(),
                        bundle_byte_length=len(durable_bundle),
                        created_at=now,
                    ),
                ]
            )
        headers = {"Authorization": f"Bearer {checkout['status_token']}"}
        first = client.get("/v1/orders/proof", headers=headers)
        second = client.get("/v1/orders/proof", headers=headers)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=2,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"newer-pending-proof",
                    proof_sha256=hashlib.sha256(b"newer-pending-proof").digest(),
                    proof_byte_length=len(b"newer-pending-proof"),
                    proof_state="calendar_pending",
                    calendar_submitted_at=now,
                    verification_metadata=None,
                    created_at=now,
                )
            )
        stale = client.get("/v1/orders/proof", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == durable_bundle
    assert stale.status_code == 409
    assert stale.content != durable_bundle
    assert bundler.receipt is None
    with context.session_factory() as session:
        audits = session.scalars(
            select(StateEvent).where(StateEvent.source == "customer_download").order_by(StateEvent.sequence)
        ).all()
        assert len(audits) == 2
        for audit in audits:
            assert audit.evidence == {
                "proof_version": 1,
                "artifact_sha256": hashlib.sha256(durable_bundle).hexdigest(),
                "artifact_kind": "persisted_verified",
            }
            serialized = json.dumps(audit.evidence)
            assert "@" not in serialized and "token" not in serialized.lower()


def test_manual_review_suppresses_historical_verification_and_download(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    now = datetime.now(UTC)
    with TestClient(context.app) as client:
        checkout = create_checkout(client)
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.payment_state = "paid"
            order.fulfillment_state = "manual_review"
            order.calendar_submitted_at = now
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"historical-proof",
                    proof_sha256=hashlib.sha256(b"historical-proof").digest(),
                    proof_byte_length=len(b"historical-proof"),
                    proof_state="bitcoin_verified",
                    calendar_submitted_at=now,
                    verification_metadata={"historical": True},
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                ProofVerification(
                    order_id=order.id,
                    proof_version=1,
                    method="fixture-exact-digest",
                    verified_at=now,
                    block_height=900000,
                    block_hash="ab" * 32,
                    block_time=now,
                    confirmation_policy="fixture-confirmed",
                    created_at=now,
                )
            )
        headers = {"Authorization": f"Bearer {checkout['status_token']}"}
        status_response = client.get("/v1/orders/status", headers=headers)
        proof_response = client.get("/v1/orders/proof", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["proof_available"] is False
    assert "bitcoin_verified_at" not in status_response.json()
    assert proof_response.status_code == 409


def test_body_content_cors_rate_and_disabled_mode_controls(app_factory: Any) -> None:
    context: ServiceContext = app_factory(checkout_rate_limit=1)
    with TestClient(context.app) as client:
        wrong_content = client.post(
            "/v1/checkout",
            content=json.dumps(checkout_payload()),
            headers={"Content-Type": "text/plain", "Idempotency-Key": idempotency_key()},
        )
        oversized = client.post(
            "/v1/checkout",
            content=b"{" + b"x" * 5000 + b"}",
            headers={"Content-Type": "application/json", "Idempotency-Key": idempotency_key()},
        )
        first = client.post(
            "/v1/checkout",
            json=checkout_payload(),
            headers={
                "Idempotency-Key": idempotency_key(),
                "Origin": "https://coa.example.test",
            },
        )
        limited = client.post(
            "/v1/checkout", json=checkout_payload(), headers={"Idempotency-Key": idempotency_key(b"other-key")}
        )
        allowed = client.options(
            "/v1/checkout",
            headers={
                "Origin": "https://coa.example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Cache-Control, Content-Type, Idempotency-Key",
            },
        )
        denied = client.options(
            "/v1/checkout",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
    assert wrong_content.status_code == 415
    assert oversized.status_code == 413
    assert first.status_code == 201
    assert limited.status_code == 429
    assert allowed.headers["access-control-allow-origin"] == "https://coa.example.test"
    allowed_headers = {value.strip().lower() for value in allowed.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "cache-control", "content-type", "idempotency-key"} <= allowed_headers
    assert first.headers["access-control-expose-headers"] == (
        "Content-Disposition, Content-Length, Cache-Control"
    )
    assert "access-control-allow-origin" not in denied.headers
    with context.session_factory() as session:
        counter = session.scalar(select(RateCounter))
        assert counter is not None and len(counter.key_hash) == 32

    disabled = create_app()
    with TestClient(disabled) as client:
        response = client.post(
            "/v1/checkout", json=checkout_payload(), headers={"Idempotency-Key": idempotency_key()}
        )
    assert response.status_code == 503
    assert "config" not in response.text and "secret" not in response.text


def test_validation_and_structured_logs_do_not_echo_sensitive_values(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter())
    logger = logging.getLogger("timestamp_service")
    logger.addHandler(handler)
    payload = checkout_payload()
    payload["unknown"] = "private-body-marker"
    try:
        with TestClient(context.app) as client:
            invalid = client.post(
                "/v1/checkout", json=payload, headers={"Idempotency-Key": idempotency_key()}
            )
            token_path = client.get("/v1/orders/v1.secret-token-marker")
    finally:
        logger.removeHandler(handler)
    logs = stream.getvalue()
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "invalid request"}
    assert token_path.status_code == 404
    for sensitive in (
        payload["email"],
        payload["manifest_sha256"],
        "private-body-marker",
        "secret-token-marker",
    ):
        assert sensitive not in invalid.text
        assert sensitive not in logs
    assert '"path":"unmatched"' in logs


def test_health_endpoints_disclose_only_state(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
    assert live.json() == {"status": "ok"}
    assert ready.json() == {"status": "ok"}
    assert "sqlite" not in ready.text
