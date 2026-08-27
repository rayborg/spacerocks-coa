from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
from collections.abc import Mapping
from typing import Any, Protocol

import stripe

from app.config.settings import AppEnvironment, PaymentMode
from app.payments.models import CanonicalCheckout, HostedCheckoutRequest, HostedCheckoutResult, ProviderEvent

STRIPE_API_VERSION = "2026-07-29.dahlia"
STRIPE_INTEGRATION_IDENTIFIER = "spacerocks_timestamp_checkout_qjvkmzrx"


class PaymentSignatureError(ValueError):
    pass


class PaymentProviderError(RuntimeError):
    pass


class PaymentProvider(Protocol):
    payment_mode: PaymentMode

    async def create_checkout(self, request: HostedCheckoutRequest, idempotency_key: str) -> HostedCheckoutResult: ...

    def verify_event(self, raw_body: bytes, signature: str, tolerance_seconds: int) -> ProviderEvent: ...

    async def resolve_internal_order_id(self, event: ProviderEvent) -> str | None: ...

    async def retrieve_checkout(self, session_id: str) -> CanonicalCheckout: ...


class StripePaymentProvider:
    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        *,
        payment_mode: PaymentMode,
        timeout_seconds: float = 10.0,
    ) -> None:
        if payment_mode not in {PaymentMode.STRIPE_TEST, PaymentMode.STRIPE_LIVE}:
            raise ValueError("Stripe provider requires an explicit Stripe payment mode")
        key_mode = "live" if payment_mode == PaymentMode.STRIPE_LIVE else "test"
        if not secret_key.startswith((f"sk_{key_mode}_", f"rk_{key_mode}_")):
            raise ValueError("Stripe credential does not match the configured payment mode")
        if not webhook_secret.startswith("whsec_"):
            raise ValueError("Stripe provider requires a webhook signing secret")
        self.payment_mode = payment_mode
        self.livemode = payment_mode == PaymentMode.STRIPE_LIVE
        self.webhook_secret = webhook_secret
        self.timeout_seconds = timeout_seconds
        self.client = stripe.StripeClient(secret_key, stripe_version=STRIPE_API_VERSION)

    async def create_checkout(self, request: HostedCheckoutRequest, idempotency_key: str) -> HostedCheckoutResult:
        try:
            session = await self._run(
                lambda: self.client.v1.checkout.sessions.create(
                    {
                        "mode": "payment",
                        "automatic_tax": {"enabled": False},
                        "integration_identifier": STRIPE_INTEGRATION_IDENTIFIER,
                        "customer_email": request.customer_email,
                        "line_items": [{"price": request.price_id, "quantity": 1}],
                        "success_url": request.success_url,
                        "cancel_url": request.cancel_url,
                        "metadata": {"order_id": request.internal_order_id},
                        "payment_intent_data": {"metadata": {"order_id": request.internal_order_id}},
                    },
                    {"idempotency_key": idempotency_key},
                )
            )
        except (TimeoutError, stripe.StripeError) as error:
            raise PaymentProviderError("Stripe checkout creation failed") from error
        session_data = _stripe_mapping(session)
        self._require_object_mode(session_data, "checkout creation")
        session_id = _required_string(session_data, "id")
        checkout_url = _required_string(session_data, "url")
        return HostedCheckoutResult(session_id=session_id, checkout_url=checkout_url)

    def verify_event(self, raw_body: bytes, signature: str, tolerance_seconds: int) -> ProviderEvent:
        try:
            event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
                raw_body,
                signature,
                self.webhook_secret,
                tolerance=tolerance_seconds,
            )
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise PaymentSignatureError("invalid webhook signature") from error
        try:
            event_data = _stripe_mapping(event)
        except PaymentProviderError as error:
            raise PaymentSignatureError("invalid webhook event shape") from error
        provider_event = _provider_event(event_data)
        if provider_event.livemode != self.livemode:
            raise PaymentSignatureError("webhook event mode does not match the configured payment mode")
        return provider_event

    async def retrieve_checkout(self, session_id: str) -> CanonicalCheckout:
        try:
            session = await self._run(
                lambda: self.client.v1.checkout.sessions.retrieve(
                    session_id,
                    {
                        "expand": [
                            "line_items.data.price",
                            "payment_intent.latest_charge.refunds",
                        ]
                    },
                )
            )
        except (TimeoutError, stripe.StripeError) as error:
            raise PaymentProviderError("Stripe checkout retrieval failed") from error
        session_data = _stripe_mapping(session)
        self._require_object_mode(session_data, "checkout retrieval")
        return _canonical_checkout(session_data)

    async def resolve_internal_order_id(self, event: ProviderEvent) -> str | None:
        if event.livemode != self.livemode:
            raise PaymentProviderError("Stripe event mode does not match the configured payment mode")
        if event.internal_order_id is not None:
            return event.internal_order_id
        if event.payment_intent_id is None:
            return None
        try:
            payment_intent = await self._run(
                lambda: self.client.v1.payment_intents.retrieve(event.payment_intent_id)
            )
        except (TimeoutError, stripe.StripeError) as error:
            raise PaymentProviderError("Stripe PaymentIntent retrieval failed") from error
        payment_intent_data = _stripe_mapping(payment_intent)
        self._require_object_mode(payment_intent_data, "PaymentIntent retrieval")
        metadata = payment_intent_data.get("metadata")
        if not isinstance(metadata, Mapping) or not metadata.get("order_id"):
            return None
        return str(metadata["order_id"])

    async def _run(self, operation: Any) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(operation), timeout=self.timeout_seconds)
        except TimeoutError as error:
            raise PaymentProviderError("Stripe request timed out") from error

    def _require_object_mode(self, value: Mapping[str, Any], operation: str) -> None:
        livemode = value.get("livemode")
        if type(livemode) is not bool or livemode != self.livemode:
            raise PaymentProviderError(f"Stripe {operation} returned an object from the wrong mode")


# Keep imports stable while composition migrates to the explicit mode-aware constructor.
StripeTestPaymentProvider = StripePaymentProvider


class FixturePaymentProvider:
    """Deterministic signed provider for APP_ENV=test; never contacts Stripe."""

    def __init__(
        self,
        *,
        app_env: AppEnvironment,
        signing_secret: bytes = b"phase0-fixture-webhook-secret-32",
    ) -> None:
        if app_env != AppEnvironment.TEST or "pytest" not in sys.modules:
            raise RuntimeError("fixture_payment_forbidden_outside_test")
        if len(signing_secret) < 32:
            raise ValueError("fixture signing secret must contain at least 32 bytes")
        self.app_env = app_env
        self.payment_mode = PaymentMode.FIXTURE
        self.signing_secret = signing_secret
        self.checkouts: dict[str, CanonicalCheckout] = {}

    async def create_checkout(self, request: HostedCheckoutRequest, idempotency_key: str) -> HostedCheckoutResult:
        session_id = f"cs_test_{hashlib.sha256(idempotency_key.encode('ascii')).hexdigest()[:24]}"
        self.checkouts[session_id] = CanonicalCheckout(
            session_id=session_id,
            payment_intent_id=None,
            livemode=False,
            mode="payment",
            status="open",
            payment_status="unpaid",
            metadata={"order_id": request.internal_order_id},
            price_id=request.price_id,
            amount_total=request.amount_minor,
            currency=request.currency,
            quantity=1,
        )
        return HostedCheckoutResult(
            session_id=session_id,
            checkout_url=f"https://checkout.stripe.com/c/pay/{session_id}",
        )

    def verify_event(self, raw_body: bytes, signature: str, tolerance_seconds: int) -> ProviderEvent:
        try:
            fields = dict(part.split("=", 1) for part in signature.split(","))
            timestamp = int(fields["t"])
            supplied = fields["v1"]
        except (KeyError, ValueError) as error:
            raise PaymentSignatureError("invalid webhook signature") from error
        if abs(int(time.time()) - timestamp) > tolerance_seconds:
            raise PaymentSignatureError("invalid webhook signature")
        expected = hmac.new(
            self.signing_secret,
            str(timestamp).encode("ascii") + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise PaymentSignatureError("invalid webhook signature")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PaymentSignatureError("invalid webhook payload") from error
        if not isinstance(payload, dict):
            raise PaymentSignatureError("invalid webhook payload")
        event = _provider_event(payload)
        if event.livemode:
            raise PaymentSignatureError("webhook event mode does not match the configured payment mode")
        return event

    async def retrieve_checkout(self, session_id: str) -> CanonicalCheckout:
        try:
            return self.checkouts[session_id]
        except KeyError as error:
            raise PaymentProviderError("fixture checkout is missing") from error

    async def resolve_internal_order_id(self, event: ProviderEvent) -> str | None:
        if event.internal_order_id is not None:
            return event.internal_order_id
        if event.payment_intent_id is None:
            return None
        for checkout in self.checkouts.values():
            if checkout.payment_intent_id == event.payment_intent_id:
                return checkout.metadata.get("order_id")
        return None

    def set_checkout(self, checkout: CanonicalCheckout) -> None:
        self.checkouts[checkout.session_id] = checkout

    def sign(self, raw_body: bytes, timestamp: int | None = None) -> str:
        signed_at = timestamp if timestamp is not None else int(time.time())
        signature = hmac.new(
            self.signing_secret,
            str(signed_at).encode("ascii") + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
        return f"t={signed_at},v1={signature}"


def _provider_event(event: Mapping[str, Any]) -> ProviderEvent:
    try:
        event_id = str(event["id"])
        event_type = str(event["type"])
        livemode = event["livemode"]
        data = event["data"]
        object_data = data["object"]
    except (KeyError, TypeError) as error:
        raise PaymentSignatureError("invalid webhook event shape") from error
    if not isinstance(livemode, bool) or not isinstance(object_data, Mapping):
        raise PaymentSignatureError("invalid webhook event shape")
    metadata = object_data.get("metadata")
    internal_order_id = (
        str(metadata.get("order_id")) if isinstance(metadata, Mapping) and metadata.get("order_id") else None
    )
    session_id = str(object_data.get("id")) if event_type.startswith("checkout.session.") else None
    payment_intent = object_data.get("payment_intent")
    payment_intent_id = _reference_id(payment_intent)
    refund_status: str | None = None
    refunded_amount: int | None = None
    if event_type.startswith("refund."):
        status = object_data.get("status")
        amount = object_data.get("amount")
        refund_status = str(status) if status is not None else None
        refunded_amount = int(amount) if type(amount) is int else None
    elif event_type == "charge.refunded":
        refund_status = "succeeded" if object_data.get("refunded") is True else None
        amount = object_data.get("amount_refunded")
        refunded_amount = int(amount) if type(amount) is int else None
    return ProviderEvent(
        event_id=event_id,
        event_type=event_type,
        livemode=livemode,
        internal_order_id=internal_order_id,
        session_id=session_id,
        payment_intent_id=payment_intent_id,
        refund_status=refund_status,
        refunded_amount=refunded_amount,
    )


def _canonical_checkout(session: Mapping[str, Any]) -> CanonicalCheckout:
    line_items = session.get("line_items")
    data = line_items.get("data") if isinstance(line_items, Mapping) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise PaymentProviderError("canonical checkout must contain exactly one line item")
    item = data[0]
    price = item.get("price")
    price_id = _reference_id(price)
    metadata = session.get("metadata")
    livemode = session.get("livemode")
    if not isinstance(metadata, Mapping) or price_id is None or type(livemode) is not bool:
        raise PaymentProviderError("canonical checkout is incomplete")
    try:
        payment_intent = session.get("payment_intent")
        charge = payment_intent.get("latest_charge") if isinstance(payment_intent, Mapping) else None
        refunded_amount_total = 0
        refund_status: str | None = None
        if isinstance(charge, Mapping):
            amount_refunded = charge.get("amount_refunded")
            refunded_amount_total = int(amount_refunded) if type(amount_refunded) is int else 0
            if refunded_amount_total > 0:
                refunds = charge.get("refunds")
                refund_data = refunds.get("data") if isinstance(refunds, Mapping) else None
                statuses = {
                    str(refund.get("status"))
                    for refund in refund_data or []
                    if isinstance(refund, Mapping)
                }
                refund_status = "succeeded" if statuses and statuses == {"succeeded"} else "pending"
        return CanonicalCheckout(
            session_id=str(session["id"]),
            payment_intent_id=_reference_id(session.get("payment_intent")),
            livemode=livemode,
            mode=str(session["mode"]),
            status=str(session["status"]),
            payment_status=str(session["payment_status"]),
            metadata={str(key): str(value) for key, value in metadata.items()},
            price_id=price_id,
            amount_total=int(session["amount_total"]),
            currency=str(session["currency"]),
            quantity=int(item["quantity"]),
            refund_status=refund_status,
            refunded_amount_total=refunded_amount_total,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PaymentProviderError("canonical checkout is incomplete") from error


def _reference_id(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and value.get("id"):
        return str(value["id"])
    return None


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise PaymentProviderError("Stripe response is incomplete")
    return result


def _stripe_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, stripe.StripeObject):
        result = value.to_dict()
        if isinstance(result, Mapping):
            return result
    raise PaymentProviderError("Stripe response has an invalid shape")
