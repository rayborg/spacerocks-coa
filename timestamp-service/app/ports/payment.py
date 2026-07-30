from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.identifiers import OrderReference, ProviderReference


@dataclass(frozen=True, slots=True)
class CheckoutParameters:
    order_reference: OrderReference
    amount_minor: int
    currency: str
    price_id: ProviderReference
    success_url: str
    cancel_url: str


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    session_reference: ProviderReference
    checkout_url: str


class PaymentGateway(Protocol):
    async def create_checkout(self, parameters: CheckoutParameters, idempotency_key: str) -> CheckoutResult: ...
