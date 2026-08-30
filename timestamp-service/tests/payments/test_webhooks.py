from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import REPOSITORY_ROOT, ServiceContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import PaymentMode
from app.db.models import DurableJob, Order, ProofVersion, StateEvent, StripeEvent
from app.payments.gateway import PaymentProviderError, PaymentSignatureError, StripePaymentProvider
from app.payments.models import CanonicalCheckout, HostedCheckoutRequest

FID_FRAGMENT = "fidkdWxOYHwnPyd1blpxYHZxWjA0SDdUN1NGPEF8"


def checkout_payload() -> dict[str, Any]:
    return json.loads(
        (REPOSITORY_ROOT / "contracts/fixtures/checkout-request.valid.json").read_text(encoding="utf-8")
    )


def idempotency_key() -> str:
    return base64.urlsafe_b64encode(b"payment-test-key").rstrip(b"=").decode("ascii")


def create_checkout(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/checkout",
        json=checkout_payload(),
        headers={"Idempotency-Key": idempotency_key()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _event(
    context: ServiceContext,
    event_type: str,
    event_id: str,
    *,
    include_metadata: bool = True,
    livemode: bool = False,
    object_changes: dict[str, object] | None = None,
) -> tuple[bytes, str]:
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.checkout_session_id is not None
        object_data: dict[str, object]
        if event_type.startswith("checkout.session."):
            object_data = {
                "id": order.checkout_session_id,
                "payment_intent": "pi_fixture_001",
                "metadata": {"order_id": str(order.id)} if include_metadata else {},
            }
        else:
            object_data = {
                "id": "provider_object_fixture",
                "payment_intent": "pi_fixture_001",
                "metadata": {"order_id": str(order.id)} if include_metadata else {},
            }
        object_data.update(object_changes or {})
    body = json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "livemode": livemode,
            "data": {"object": object_data},
        },
        separators=(",", ":"),
    ).encode()
    return body, context.provider.sign(body)


def _canonical(context: ServiceContext, **changes: object) -> CanonicalCheckout:
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.checkout_session_id is not None
        original = context.provider.checkouts[order.checkout_session_id]
    values: dict[str, object] = {
        "payment_intent_id": "pi_fixture_001",
        "status": "complete",
        "payment_status": "paid",
    }
    values.update(changes)
    canonical = replace(original, **values)
    context.provider.set_checkout(canonical)
    return canonical


def _post_event(client: TestClient, body: bytes, signature: str):
    return client.post(
        "/v1/webhooks/stripe",
        content=body,
        headers={"Content-Type": "application/json", "Stripe-Signature": signature},
    )


def test_verified_completed_event_queues_exactly_one_job_and_duplicates_are_safe(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        body, signature = _event(context, "checkout.session.completed", "evt_paid_001")
        first = _post_event(client, body, signature)
        duplicate = _post_event(client, body, signature)
    assert first.status_code == 200
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None
        assert order.payment_state == "paid"
        assert order.fulfillment_state == "queued"
        assert order.payment_intent_id == "pi_fixture_001"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 1
        assert session.scalar(select(func.count()).select_from(StripeEvent)) == 1


def test_exact_duplicate_avoids_provider_outage_but_tampered_event_id_is_rejected(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        body, signature = _event(context, "checkout.session.completed", "evt_outage_replay")
        assert _post_event(client, body, signature).status_code == 200
        retrievals = 0

        async def unavailable(_session_id: str) -> CanonicalCheckout:
            nonlocal retrievals
            retrievals += 1
            raise PaymentProviderError("provider unavailable")

        context.provider.retrieve_checkout = unavailable  # type: ignore[method-assign]
        duplicate = _post_event(client, body, signature)
        payload = json.loads(body)
        payload["data"]["object"]["metadata"]["tampered"] = "true"
        tampered_body = json.dumps(payload, separators=(",", ":")).encode()
        tampered = _post_event(client, tampered_body, context.provider.sign(tampered_body))
    assert duplicate.status_code == 200
    assert duplicate.json() == {"received": True, "duplicate": True}
    assert tampered.status_code == 400
    assert retrievals == 0
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StripeEvent)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"amount_total": 999},
        {"currency": "eur"},
        {"quantity": 2},
        {"price_id": "wrong_price"},
        {"metadata": {"order_id": "00000000-0000-0000-0000-000000000000"}},
        {"livemode": True},
        {"mode": "subscription"},
        {"status": "open"},
    ],
)
def test_wrong_canonical_payment_attributes_never_authorize(app_factory: Any, change: dict[str, object]) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context, **change)
        body, signature = _event(context, "checkout.session.completed", "evt_wrong_attribute")
        response = _post_event(client, body, signature)
    assert response.status_code == 400
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.payment_state == "checkout_open"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 0
        event = session.scalar(select(StripeEvent))
        assert event is not None and event.safe_error_code == "payment_binding_invalid"


def test_live_event_never_authorizes_fixture_order(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    retrievals = 0

    async def retrieve(_session_id: str) -> CanonicalCheckout:
        nonlocal retrievals
        retrievals += 1
        raise AssertionError("wrong-mode webhook must be rejected before canonical retrieval")

    context.provider.retrieve_checkout = retrieve  # type: ignore[method-assign]
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        body, signature = _event(
            context,
            "checkout.session.completed",
            "evt_live_rejected",
            livemode=True,
        )
        response = _post_event(client, body, signature)
    assert response.status_code == 400
    assert retrievals == 0
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.payment_state == "checkout_open"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 0
        assert session.scalar(select(func.count()).select_from(StripeEvent)) == 0


def test_invalid_stale_and_modified_signatures_fail_before_persistence(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        body, signature = _event(context, "checkout.session.completed", "evt_signature")
        invalid = _post_event(client, body, "t=1,v1=invalid")
        stale = _post_event(client, body, context.provider.sign(body, timestamp=int(time.time()) - 1000))
        modified = _post_event(client, body + b" ", signature)
    assert {invalid.status_code, stale.status_code, modified.status_code} == {400}
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StripeEvent)) == 0
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 0


def test_processing_async_success_and_out_of_order_failure_do_not_regress(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context, payment_status="unpaid", status="complete")
        processing_body, processing_signature = _event(
            context, "checkout.session.completed", "evt_processing"
        )
        assert _post_event(client, processing_body, processing_signature).status_code == 200
        _canonical(context)
        paid_body, paid_signature = _event(
            context, "checkout.session.async_payment_succeeded", "evt_async_paid"
        )
        assert _post_event(client, paid_body, paid_signature).status_code == 200
        failed_body, failed_signature = _event(
            context, "checkout.session.async_payment_failed", "evt_late_failure"
        )
        assert _post_event(client, failed_body, failed_signature).status_code == 200
        expired_body, expired_signature = _event(context, "checkout.session.expired", "evt_late_expired")
        assert _post_event(client, expired_body, expired_signature).status_code == 400
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.payment_state == "paid"
        assert order.fulfillment_state == "queued"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 1


def test_expiration_before_payment_never_queues_fulfillment(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        canonical = _canonical(context, payment_status="unpaid", status="expired")
        context.provider.set_checkout(canonical)
        body, signature = _event(context, "checkout.session.expired", "evt_expired")
        assert _post_event(client, body, signature).status_code == 200
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.payment_state == "expired"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 0


def test_dispute_and_refund_preserve_fulfillment_and_proof_evidence(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        paid_body, paid_signature = _event(context, "checkout.session.completed", "evt_paid_for_dispute")
        assert _post_event(client, paid_body, paid_signature).status_code == 200
        with context.session_factory() as session, session.begin():
            order = session.scalar(select(Order))
            assert order is not None
            order.fulfillment_state = "delivered"
            session.add(
                ProofVersion(
                    order_id=order.id,
                    version=1,
                    target_digest=order.manifest_digest,
                    proof_bytes=b"proof",
                    proof_sha256=b"p" * 32,
                    proof_byte_length=5,
                    proof_state="calendar_pending",
                    calendar_submitted_at=order.updated_at,
                    verification_metadata=None,
                    created_at=order.updated_at,
                )
            )
        dispute_body, dispute_signature = _event(
            context, "charge.dispute.created", "evt_dispute", include_metadata=False
        )
        assert _post_event(client, dispute_body, dispute_signature).status_code == 200
        _canonical(context, refund_status="succeeded", refunded_amount_total=500)
        refund_body, refund_signature = _event(
            context,
            "charge.refunded",
            "evt_refund",
            include_metadata=False,
            object_changes={"refunded": True, "amount_refunded": 500},
        )
        assert _post_event(client, refund_body, refund_signature).status_code == 200
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None
        assert order.payment_state == "refunded"
        assert order.fulfillment_state == "delivered"
        assert session.scalar(select(func.count()).select_from(ProofVersion)) == 1


@pytest.mark.parametrize("event_type", ["charge.dispute.created", "charge.refunded"])
def test_refund_or_dispute_arriving_before_paid_event_blocks_later_fulfillment(
    app_factory: Any, event_type: str
) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        if event_type == "charge.refunded":
            _canonical(context, refund_status="succeeded", refunded_amount_total=500)
            object_changes = {"refunded": True, "amount_refunded": 500}
        else:
            _canonical(context)
            object_changes = None
        commercial_body, commercial_signature = _event(
            context,
            event_type,
            "evt_commercial_first",
            include_metadata=False,
            object_changes=object_changes,
        )
        assert _post_event(client, commercial_body, commercial_signature).status_code == 200
        paid_body, paid_signature = _event(context, "checkout.session.completed", "evt_paid_late")
        assert _post_event(client, paid_body, paid_signature).status_code == 200
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        expected = "disputed" if event_type == "charge.dispute.created" else "refunded"
        assert order is not None and order.payment_state == expected
        assert order.fulfillment_state == "awaiting_payment"
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 0


@pytest.mark.parametrize(
    ("event_status", "event_amount", "canonical_status", "canonical_amount", "expected_state"),
    [
        ("succeeded", 100, "succeeded", 100, "paid"),
        ("pending", 500, "pending", 500, "paid"),
        ("failed", 500, "pending", 500, "paid"),
        ("canceled", 500, "pending", 500, "paid"),
        ("succeeded", 500, "succeeded", 500, "refunded"),
    ],
)
def test_only_succeeded_full_cumulative_refund_changes_payment_state(
    app_factory: Any,
    event_status: str,
    event_amount: int,
    canonical_status: str,
    canonical_amount: int,
    expected_state: str,
) -> None:
    context: ServiceContext = app_factory()
    with TestClient(context.app) as client:
        create_checkout(client)
        _canonical(context)
        paid_body, paid_signature = _event(context, "checkout.session.completed", "evt_paid_for_refund")
        assert _post_event(client, paid_body, paid_signature).status_code == 200
        _canonical(
            context,
            refund_status=canonical_status,
            refunded_amount_total=canonical_amount,
        )
        refund_body, refund_signature = _event(
            context,
            "refund.updated",
            f"evt_refund_{event_status}_{event_amount}",
            include_metadata=False,
            object_changes={"status": event_status, "amount": event_amount},
        )
        assert _post_event(client, refund_body, refund_signature).status_code == 200
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None and order.payment_state == expected_state


def test_concurrent_duplicate_event_produces_one_event_and_job(app_factory: Any, tmp_path: Path) -> None:
    context: ServiceContext = app_factory(sqlite_url=f"sqlite:///{tmp_path / 'concurrent.db'}")
    with TestClient(context.app) as client:
        create_checkout(client)
    _canonical(context)
    body, signature = _event(context, "checkout.session.completed", "evt_concurrent")

    def deliver() -> int:
        with TestClient(context.app) as client:
            return _post_event(client, body, signature).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _index: deliver(), range(2)))
    assert statuses == [200, 200]
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StripeEvent)) == 1
        assert session.scalar(select(func.count()).select_from(DurableJob)) == 1
        transition_count = session.scalar(
            select(func.count()).select_from(StateEvent).where(StateEvent.source == "stripe_webhook")
        )
        assert transition_count == 1


def test_official_stripe_signature_verification_is_raw_body_bound() -> None:
    secret = "whsec_phase0_test_only"
    provider = StripePaymentProvider(
        "rk_test_phase0",
        secret,
        payment_mode=PaymentMode.STRIPE_TEST,
    )
    body = json.dumps(
        {
            "id": "evt_official_signature",
            "object": "event",
            "type": "checkout.session.completed",
            "livemode": False,
            "data": {"object": {"id": "cs_test_1", "metadata": {"order_id": "opaque"}}},
        },
        separators=(",", ":"),
    ).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(),
        str(timestamp).encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={signature}"
    assert provider.verify_event(body, header, 300).event_id == "evt_official_signature"
    with pytest.raises(PaymentSignatureError):
        provider.verify_event(body + b" ", header, 300)


@pytest.mark.asyncio
async def test_stripe_sdk_operation_runs_off_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    event_loop_thread = threading.get_ident()
    sdk_threads: list[int] = []

    def create(_params: object, _options: object) -> dict[str, object]:
        sdk_threads.append(threading.get_ident())
        return {
            "id": "cs_test_threaded",
            "url": f"https://checkout.stripe.com/c/pay/cs_test_threaded#{FID_FRAGMENT}",
            "livemode": False,
        }

    provider = StripePaymentProvider(
        "rk_test_phase0",
        "whsec_phase0",
        payment_mode=PaymentMode.STRIPE_TEST,
        timeout_seconds=1,
    )
    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", create)
    result = await provider.create_checkout(
        HostedCheckoutRequest(
            internal_order_id="00000000-0000-0000-0000-000000000001",
            order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
            customer_email="customer@example.test",
            price_id="price_test",
            amount_minor=500,
            currency="usd",
            success_url="https://example.test/success",
            cancel_url="https://example.test/cancel",
        ),
        "provider-idempotency-key",
    )
    assert result.session_id == "cs_test_threaded"
    assert sdk_threads and sdk_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_stripe_sdk_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_create(_params: object, _options: object) -> dict[str, object]:
        time.sleep(0.05)
        return {
            "id": "cs_test_late",
            "url": f"https://checkout.stripe.com/c/pay/cs_test_late#{FID_FRAGMENT}",
            "livemode": False,
        }

    provider = StripePaymentProvider(
        "rk_test_phase0",
        "whsec_phase0",
        payment_mode=PaymentMode.STRIPE_TEST,
        timeout_seconds=0.001,
    )
    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", slow_create)
    with pytest.raises(PaymentProviderError, match="timed out"):
        await provider.create_checkout(
            HostedCheckoutRequest(
                internal_order_id="00000000-0000-0000-0000-000000000001",
                order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
                customer_email="customer@example.test",
                price_id="price_test",
                amount_minor=500,
                currency="usd",
                success_url="https://example.test/success",
                cancel_url="https://example.test/cancel",
            ),
            "provider-idempotency-key",
        )


@pytest.mark.asyncio
async def test_stripe_canonical_checkout_expands_and_sums_succeeded_refunds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_expand: list[str] = []

    def retrieve(_session_id: str, params: dict[str, object]) -> dict[str, object]:
        expand = params["expand"]
        assert isinstance(expand, list)
        captured_expand.extend(str(value) for value in expand)
        return {
            "id": "cs_test_refunded",
            "payment_intent": {
                "id": "pi_fixture_001",
                "latest_charge": {
                    "amount_refunded": 500,
                    "refunds": {"data": [{"status": "succeeded"}]},
                },
            },
            "livemode": False,
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "metadata": {"order_id": "00000000-0000-0000-0000-000000000001"},
            "amount_total": 500,
            "currency": "usd",
            "line_items": {"data": [{"price": {"id": "price_test"}, "quantity": 1}]},
        }

    provider = StripePaymentProvider(
        "rk_test_phase0",
        "whsec_phase0",
        payment_mode=PaymentMode.STRIPE_TEST,
        timeout_seconds=1,
    )
    monkeypatch.setattr(provider.client.v1.checkout.sessions, "retrieve", retrieve)
    canonical = await provider.retrieve_checkout("cs_test_refunded")
    assert canonical.refund_status == "succeeded"
    assert canonical.refunded_amount_total == canonical.amount_total == 500
    assert "payment_intent.latest_charge.refunds" in captured_expand
