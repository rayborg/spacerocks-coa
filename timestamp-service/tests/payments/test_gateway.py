from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import REPOSITORY_ROOT, ServiceContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.config.settings import PaymentMode
from app.db.models import IdempotencyRequest, Order, OrderToken
from app.payments.gateway import (
    STRIPE_API_VERSION,
    STRIPE_INTEGRATION_IDENTIFIER,
    PaymentProviderError,
    PaymentSignatureError,
    StripePaymentProvider,
)
from app.payments.models import HostedCheckoutRequest, ProviderEvent
from app.payments.service import _validate_checkout_url

FID_FRAGMENT = "fidkdWxOYHwnPyd1blpxYHZxWjA0SDdUN1NGPEF8"


def _request() -> HostedCheckoutRequest:
    return HostedCheckoutRequest(
        internal_order_id="9d2ebd3f-3c9a-4aee-8ab6-6042c04a4658",
        order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
        customer_email="customer@example.test",
        price_id="price_server_controlled",
        amount_minor=500,
        currency="usd",
        success_url="https://coa.example.test/status",
        cancel_url="https://coa.example.test/cancelled",
    )


def _provider(
    payment_mode: PaymentMode = PaymentMode.STRIPE_TEST,
    *,
    key: str | None = None,
) -> StripePaymentProvider:
    default_key = "rk_live_restricted" if payment_mode == PaymentMode.STRIPE_LIVE else "rk_test_restricted"
    return StripePaymentProvider(
        key or default_key,
        "whsec_test_only",
        payment_mode=payment_mode,
    )


@pytest.mark.asyncio
async def test_stripe_checkout_is_entirely_server_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_params: dict[str, Any] = {}
    captured_options: dict[str, Any] = {}

    def fake_create(params: dict[str, Any], options: dict[str, Any]) -> dict[str, object]:
        captured_params.update(params)
        captured_options.update(options)
        return {
            "id": "cs_test_fixture",
            "url": f"https://checkout.stripe.com/c/pay/cs_test_fixture#{FID_FRAGMENT}",
            "livemode": False,
        }

    provider = _provider()
    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", fake_create)
    request = _request()
    result = await provider.create_checkout(request, "derived-idempotency-hash")
    assert result.session_id == "cs_test_fixture"
    assert captured_params["mode"] == "payment"
    assert "payment_method_types" not in captured_params
    assert captured_params["automatic_tax"] == {"enabled": False}
    assert captured_params["integration_identifier"] == STRIPE_INTEGRATION_IDENTIFIER
    assert STRIPE_INTEGRATION_IDENTIFIER.endswith("_qjvkmzrx")
    assert len(STRIPE_INTEGRATION_IDENTIFIER) <= 64
    assert captured_params["customer_email"] == "customer@example.test"
    assert captured_params["line_items"] == [{"price": "price_server_controlled", "quantity": 1}]
    assert captured_params["metadata"] == {"order_id": request.internal_order_id}
    assert captured_params["payment_intent_data"] == {"metadata": {"order_id": request.internal_order_id}}
    assert "amount" not in captured_params and "currency" not in captured_params
    assert request.order_reference not in str(captured_params["metadata"])
    assert captured_options == {"idempotency_key": "derived-idempotency-hash"}
    assert provider.client._requestor._options.stripe_version == STRIPE_API_VERSION


@pytest.mark.parametrize(
    ("payment_mode", "key"),
    [
        (PaymentMode.STRIPE_TEST, "sk_test_secret"),
        (PaymentMode.STRIPE_TEST, "rk_test_restricted"),
        (PaymentMode.STRIPE_LIVE, "sk_live_secret"),
        (PaymentMode.STRIPE_LIVE, "rk_live_restricted"),
    ],
)
def test_stripe_provider_accepts_secret_and_restricted_keys_for_its_mode(
    payment_mode: PaymentMode,
    key: str,
) -> None:
    assert _provider(payment_mode, key=key).payment_mode == payment_mode


@pytest.mark.parametrize(
    ("payment_mode", "key"),
    [
        (PaymentMode.STRIPE_TEST, "sk_live_wrong"),
        (PaymentMode.STRIPE_TEST, "rk_live_wrong"),
        (PaymentMode.STRIPE_TEST, "pk_test_publishable"),
        (PaymentMode.STRIPE_LIVE, "sk_test_wrong"),
        (PaymentMode.STRIPE_LIVE, "rk_test_wrong"),
        (PaymentMode.STRIPE_LIVE, "pk_live_publishable"),
    ],
)
def test_stripe_provider_rejects_wrong_mode_and_publishable_keys(
    payment_mode: PaymentMode,
    key: str,
) -> None:
    with pytest.raises(ValueError, match="does not match"):
        _provider(payment_mode, key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payment_mode", "returned_livemode"),
    [(PaymentMode.STRIPE_TEST, True), (PaymentMode.STRIPE_LIVE, False)],
)
async def test_stripe_checkout_creation_rejects_wrong_mode_object(
    monkeypatch: pytest.MonkeyPatch,
    payment_mode: PaymentMode,
    returned_livemode: bool,
) -> None:
    provider = _provider(payment_mode)

    def fake_create(_params: object, _options: object) -> dict[str, object]:
        return {
            "id": "cs_wrong_mode",
            "url": "https://checkout.stripe.com/c/pay/wrong",
            "livemode": returned_livemode,
        }

    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", fake_create)
    with pytest.raises(PaymentProviderError, match="wrong mode"):
        await provider.create_checkout(_request(), "mode-bound-key")


@pytest.mark.parametrize(
    ("payment_mode", "event_livemode"),
    [(PaymentMode.STRIPE_TEST, True), (PaymentMode.STRIPE_LIVE, False)],
)
def test_stripe_provider_rejects_validly_signed_wrong_mode_event(
    payment_mode: PaymentMode,
    event_livemode: bool,
) -> None:
    secret = "whsec_test_only"
    provider = StripePaymentProvider(
        "rk_live_restricted" if payment_mode == PaymentMode.STRIPE_LIVE else "rk_test_restricted",
        secret,
        payment_mode=payment_mode,
    )
    body = json.dumps(
        {
            "id": "evt_wrong_mode",
            "object": "event",
            "type": "checkout.session.completed",
            "livemode": event_livemode,
            "data": {"object": {"id": "cs_mode", "metadata": {"order_id": "opaque"}}},
        },
        separators=(",", ":"),
    ).encode()
    timestamp = int(time.time())
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    with pytest.raises(PaymentSignatureError, match="mode"):
        provider.verify_event(body, f"t={timestamp},v1={digest}", 300)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payment_mode", "returned_livemode"),
    [(PaymentMode.STRIPE_TEST, True), (PaymentMode.STRIPE_LIVE, False)],
)
async def test_payment_intent_retrieval_rejects_wrong_mode_object(
    monkeypatch: pytest.MonkeyPatch,
    payment_mode: PaymentMode,
    returned_livemode: bool,
) -> None:
    provider = _provider(payment_mode)
    monkeypatch.setattr(
        provider.client.v1.payment_intents,
        "retrieve",
        lambda _intent_id: {"livemode": returned_livemode, "metadata": {"order_id": "opaque"}},
    )
    event = ProviderEvent(
        event_id="evt_payment_intent",
        event_type="charge.dispute.created",
        livemode=payment_mode == PaymentMode.STRIPE_LIVE,
        internal_order_id=None,
        session_id=None,
        payment_intent_id="pi_mode_bound",
    )
    with pytest.raises(PaymentProviderError, match="wrong mode"):
        await provider.resolve_internal_order_id(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payment_mode", "returned_livemode"),
    [(PaymentMode.STRIPE_TEST, True), (PaymentMode.STRIPE_LIVE, False)],
)
async def test_checkout_retrieval_rejects_wrong_mode_object(
    monkeypatch: pytest.MonkeyPatch,
    payment_mode: PaymentMode,
    returned_livemode: bool,
) -> None:
    provider = _provider(payment_mode)
    monkeypatch.setattr(
        provider.client.v1.checkout.sessions,
        "retrieve",
        lambda _session_id, _params: {"livemode": returned_livemode},
    )
    with pytest.raises(PaymentProviderError, match="wrong mode"):
        await provider.retrieve_checkout("cs_mode_bound")


def test_existing_order_mode_mismatch_never_replays_or_calls_provider(
    app_factory: Any,
) -> None:
    context: ServiceContext = app_factory()
    key = base64.urlsafe_b64encode(b"mode-binding-key").rstrip(b"=").decode("ascii")
    payload = json.loads(
        (REPOSITORY_ROOT / "contracts/fixtures/checkout-request.valid.json").read_text(encoding="utf-8")
    )
    with TestClient(context.app) as client:
        first = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})
        assert first.status_code == 201
        with context.session_factory() as session, session.begin():
            assert session.scalar(select(Order.id)) is not None
            session.execute(update(Order).values(payment_mode=PaymentMode.STRIPE_LIVE.value))
        provider_calls = 0

        async def fail_if_called(_request: HostedCheckoutRequest, _idempotency_key: str) -> None:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("provider must not be called for a mismatched persisted order")

        context.provider.create_checkout = fail_if_called  # type: ignore[method-assign]
        replay = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})

    assert replay.status_code == 409
    assert "checkout_url" not in replay.text and "status_token" not in replay.text
    assert provider_calls == 0
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(OrderToken)) == 1


def test_tampered_persisted_checkout_url_never_replays_or_rotates_token(app_factory: Any) -> None:
    context: ServiceContext = app_factory()
    key = base64.urlsafe_b64encode(b"url-binding-key-1").rstrip(b"=").decode("ascii")
    payload = json.loads(
        (REPOSITORY_ROOT / "contracts/fixtures/checkout-request.valid.json").read_text(encoding="utf-8")
    )
    with TestClient(context.app) as client:
        first = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})
        assert first.status_code == 201
        with context.session_factory() as session, session.begin():
            reservation = session.scalar(select(IdempotencyRequest))
            assert reservation is not None and reservation.response_body is not None
            reservation.response_body = {
                **reservation.response_body,
                "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_alternate#fidUnsafeReplayValue123",
            }
            reservation.checkout_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        replay = client.post("/v1/checkout", json=payload, headers={"Idempotency-Key": key})

    assert replay.status_code == 409
    assert "checkout_url" not in replay.text and "status_token" not in replay.text
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(OrderToken)) == 1


@pytest.mark.parametrize(
    ("session_id", "payment_mode"),
    [
        ("cs_test_a1SafeSession9", PaymentMode.FIXTURE),
        ("cs_test_a1SafeSession9", PaymentMode.STRIPE_TEST),
        ("cs_live_a1SafeSession9", PaymentMode.STRIPE_LIVE),
    ],
)
def test_checkout_url_accepts_exact_mode_bound_stripe_session_with_fid_fragment(
    session_id: str,
    payment_mode: PaymentMode,
) -> None:
    _validate_checkout_url(
        f"https://checkout.stripe.com/c/pay/{session_id}#{FID_FRAGMENT}",
        session_id,
        payment_mode,
    )


def test_checkout_url_accepts_exact_session_path_without_optional_fragment() -> None:
    session_id = "cs_test_a1SafeSession9"
    _validate_checkout_url(
        f"https://checkout.stripe.com/c/pay/{session_id}",
        session_id,
        PaymentMode.STRIPE_TEST,
    )


@pytest.mark.parametrize(
    ("checkout_url", "session_id", "payment_mode"),
    [
        (
            f"https://checkout.stripe.com/c/pay/cs_test_safe?next=evil#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com.evil.test/c/pay/cs_test_safe#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://user:pass@checkout.stripe.com/c/pay/cs_test_safe#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com:443/c/pay/cs_test_safe#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com/c/pay/cs_test_safe/../cs_test_other#{FID_FRAGMENT}",
            "cs_test_other",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com/c/pay/%2e%2e/cs_test_safe#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com/c/pay/cs_test_other#{FID_FRAGMENT}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com/c/pay/cs_live_safe#{FID_FRAGMENT}",
            "cs_live_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            f"https://checkout.stripe.com/c/pay/cs_test_safe#{'fid' + 'a' * 1537}",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            "https://checkout.stripe.com/c/pay/cs_test_safe#fidInvalid!FragmentValue",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            "https://checkout.stripe.com/c/pay/cs_test_safe#opaqueFragmentValue123",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            "https://checkout.stripe.com/c/pay/cs_test_safe#fidshort",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
        (
            "https://checkout.stripe.com/c/pay/cs_test_safe#",
            "cs_test_safe",
            PaymentMode.STRIPE_TEST,
        ),
    ],
)
def test_checkout_url_rejects_unsafe_or_unbound_destination(
    checkout_url: str,
    session_id: str,
    payment_mode: PaymentMode,
) -> None:
    with pytest.raises(PaymentProviderError, match="unsafe checkout URL"):
        _validate_checkout_url(checkout_url, session_id, payment_mode)
