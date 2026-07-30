from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.domain.order import FulfillmentState, OrderSnapshot, OrderState, PaymentState


def snapshot() -> OrderSnapshot:
    return OrderSnapshot(
        order_reference=OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        certificate_reference=CertificateReference("AZ-2019-0447-HE"),
        manifest_digest=ManifestDigest.from_hex("ab" * 32),
        email="customer@example.test",
        amount_minor=500,
        currency="usd",
        product_version="phase0",
        payment_mode="fixture",
        consent_terms_version="v1",
        consent_privacy_version="v1",
        consent_accepted_at=datetime(2026, 7, 30, tzinfo=UTC),
    )


def test_digest_conversion_returns_exact_bytes_without_rehashing() -> None:
    digest = ManifestDigest.from_hex("00" * 31 + "ff")
    assert digest.ots_target() == b"\x00" * 31 + b"\xff"
    assert digest.hex == "00" * 31 + "ff"
    with pytest.raises(ValueError):
        ManifestDigest.from_hex("AA" * 32)
    with pytest.raises(ValueError):
        ManifestDigest.from_bytes(b"x" * 31)


def test_transitions_are_monotonic_and_idempotent() -> None:
    state = OrderState(snapshot())
    assert state.transition_payment(PaymentState.CHECKOUT_OPEN) is state
    with pytest.raises(ValueError, match="before payment"):
        state.transition_fulfillment(FulfillmentState.QUEUED)
    state = state.transition_payment(PaymentState.PAID)
    state = state.transition_fulfillment(FulfillmentState.QUEUED)
    state = state.transition_fulfillment(FulfillmentState.STAMPING)
    state = state.transition_fulfillment(FulfillmentState.CALENDAR_PENDING)
    with pytest.raises(ValueError, match="illegal fulfillment"):
        state.transition_fulfillment(FulfillmentState.STAMPING)


@pytest.mark.parametrize("commercial_state", [PaymentState.REFUNDED, PaymentState.DISPUTED])
def test_refund_and_dispute_preserve_verified_evidence(commercial_state: PaymentState) -> None:
    state = OrderState(snapshot()).transition_payment(PaymentState.PAID)
    for fulfillment in (
        FulfillmentState.QUEUED,
        FulfillmentState.STAMPING,
        FulfillmentState.CALENDAR_PENDING,
        FulfillmentState.BITCOIN_VERIFIED,
        FulfillmentState.DELIVERED,
    ):
        state = state.transition_fulfillment(fulfillment)
    changed = state.transition_payment(commercial_state)
    assert changed.fulfillment == FulfillmentState.DELIVERED
    with pytest.raises(ValueError):
        changed.transition_payment(PaymentState.PAID)
