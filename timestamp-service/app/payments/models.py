from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HostedCheckoutRequest:
    internal_order_id: str
    order_reference: str
    customer_email: str
    price_id: str
    amount_minor: int
    currency: str
    success_url: str
    cancel_url: str


@dataclass(frozen=True, slots=True)
class HostedCheckoutResult:
    session_id: str
    checkout_url: str


@dataclass(frozen=True, slots=True)
class ProviderPrice:
    price_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_id: str
    event_type: str
    livemode: bool
    internal_order_id: str | None
    session_id: str | None
    payment_intent_id: str | None
    refund_status: str | None = None
    refunded_amount: int | None = None


@dataclass(frozen=True, slots=True)
class CanonicalCheckout:
    session_id: str
    payment_intent_id: str | None
    livemode: bool
    mode: str
    status: str
    payment_status: str
    metadata: dict[str, str]
    price_id: str
    amount_total: int
    currency: str
    quantity: int
    refund_status: str | None = None
    refunded_amount_total: int = 0
