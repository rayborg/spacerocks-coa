from __future__ import annotations

from typing import Any

import pytest

from app.payments.gateway import StripeTestPaymentProvider
from app.payments.models import HostedCheckoutRequest


@pytest.mark.asyncio
async def test_stripe_checkout_is_entirely_server_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"id": "cs_test_fixture", "url": "https://checkout.stripe.com/c/pay/cs_test_fixture"}

    monkeypatch.setattr("app.payments.gateway.stripe.checkout.Session.create", fake_create)
    provider = StripeTestPaymentProvider("sk_test_phase0", "whsec_phase0")
    request = HostedCheckoutRequest(
        internal_order_id="9d2ebd3f-3c9a-4aee-8ab6-6042c04a4658",
        order_reference="ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
        customer_email="customer@example.test",
        price_id="price_server_controlled",
        amount_minor=500,
        currency="usd",
        success_url="https://coa.example.test/status",
        cancel_url="https://coa.example.test/cancelled",
    )
    result = await provider.create_checkout(request, "derived-idempotency-hash")
    assert result.session_id == "cs_test_fixture"
    assert captured["mode"] == "payment"
    assert captured["payment_method_types"] == ["card"]
    assert captured["customer_email"] == "customer@example.test"
    assert captured["line_items"] == [{"price": "price_server_controlled", "quantity": 1}]
    assert captured["metadata"] == {"order_id": request.internal_order_id}
    assert captured["payment_intent_data"] == {"metadata": {"order_id": request.internal_order_id}}
    assert "amount" not in captured and "currency" not in captured
    assert request.order_reference not in str(captured["metadata"])
