from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference


class PaymentState(StrEnum):
    CHECKOUT_OPEN = "checkout_open"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class FulfillmentState(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    QUEUED = "queued"
    STAMPING = "stamping"
    CALENDAR_PENDING = "calendar_pending"
    BITCOIN_VERIFIED = "bitcoin_verified"
    DELIVERED = "delivered"
    MANUAL_REVIEW = "manual_review"


_PAYMENT_TRANSITIONS: dict[PaymentState, frozenset[PaymentState]] = {
    PaymentState.CHECKOUT_OPEN: frozenset(
        {
            PaymentState.PROCESSING,
            PaymentState.PAID,
            PaymentState.FAILED,
            PaymentState.EXPIRED,
            PaymentState.REFUNDED,
            PaymentState.DISPUTED,
        }
    ),
    PaymentState.PROCESSING: frozenset(
        {
            PaymentState.PAID,
            PaymentState.FAILED,
            PaymentState.EXPIRED,
            PaymentState.REFUNDED,
            PaymentState.DISPUTED,
        }
    ),
    PaymentState.PAID: frozenset({PaymentState.REFUNDED, PaymentState.DISPUTED}),
    PaymentState.DISPUTED: frozenset({PaymentState.REFUNDED}),
    PaymentState.FAILED: frozenset(),
    PaymentState.EXPIRED: frozenset(),
    PaymentState.REFUNDED: frozenset(),
}

_FULFILLMENT_TRANSITIONS: dict[FulfillmentState, frozenset[FulfillmentState]] = {
    FulfillmentState.AWAITING_PAYMENT: frozenset({FulfillmentState.QUEUED, FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.QUEUED: frozenset({FulfillmentState.STAMPING, FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.STAMPING: frozenset({FulfillmentState.CALENDAR_PENDING, FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.CALENDAR_PENDING: frozenset({FulfillmentState.BITCOIN_VERIFIED, FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.BITCOIN_VERIFIED: frozenset({FulfillmentState.DELIVERED, FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.DELIVERED: frozenset({FulfillmentState.MANUAL_REVIEW}),
    FulfillmentState.MANUAL_REVIEW: frozenset(),
}


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    order_reference: OrderReference
    certificate_reference: CertificateReference
    manifest_digest: ManifestDigest
    email: str
    amount_minor: int
    currency: str
    product_version: str
    payment_mode: str
    consent_terms_version: str
    consent_privacy_version: str
    consent_accepted_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.email) <= 254:
            raise ValueError("email must contain 1 through 254 characters")
        if self.amount_minor < 0:
            raise ValueError("amount cannot be negative")
        if len(self.currency) != 3 or self.currency.lower() != self.currency or not self.currency.isalpha():
            raise ValueError("currency must be a lowercase ISO-style code")


@dataclass(frozen=True, slots=True)
class OrderState:
    snapshot: OrderSnapshot
    payment: PaymentState = PaymentState.CHECKOUT_OPEN
    fulfillment: FulfillmentState = FulfillmentState.AWAITING_PAYMENT

    def transition_payment(self, target: PaymentState) -> OrderState:
        if target == self.payment:
            return self
        if target not in _PAYMENT_TRANSITIONS[self.payment]:
            raise ValueError(f"illegal payment transition: {self.payment} -> {target}")
        return replace(self, payment=target)

    def transition_fulfillment(self, target: FulfillmentState) -> OrderState:
        if target == self.fulfillment:
            return self
        if target not in _FULFILLMENT_TRANSITIONS[self.fulfillment]:
            raise ValueError(f"illegal fulfillment transition: {self.fulfillment} -> {target}")
        starts_fulfillment = self.fulfillment == FulfillmentState.AWAITING_PAYMENT and target == FulfillmentState.QUEUED
        if self.payment != PaymentState.PAID and starts_fulfillment:
            raise ValueError("fulfillment cannot be queued before payment")
        return replace(self, fulfillment=target)
