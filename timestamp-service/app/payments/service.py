from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config.settings import PaymentMode, Settings
from app.db.models import DurableJob, IdempotencyRequest, Order, StripeEvent
from app.db.repositories import OrderStore, enqueue_fulfillment_once, enqueue_outbox_once, record_stripe_event
from app.domain.order import FulfillmentState, PaymentState
from app.payments.gateway import PaymentProvider, PaymentProviderError
from app.payments.models import CanonicalCheckout, HostedCheckoutRequest, HostedCheckoutResult, ProviderEvent
from app.security.idempotency import IdempotencyBinding

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CheckoutUnavailable(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class CheckoutInProgress(RuntimeError):
    pass


class WebhookRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CheckoutInput:
    certificate_reference: str
    manifest_digest: bytes
    email: str
    terms_version: str
    privacy_version: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class CheckoutOutput:
    order_reference: str
    status_token: str
    checkout_url: str
    payment_state: str
    fulfillment_state: str


@dataclass(frozen=True, slots=True)
class CheckoutPlan:
    reservation_id: uuid.UUID
    order_id: uuid.UUID
    order_reference: str
    customer_email: str
    price_id: str
    amount_minor: int
    currency: str
    success_url: str
    cancel_url: str
    provider_idempotency_key: str
    lease_id: str


@dataclass(frozen=True, slots=True)
class WebhookResult:
    duplicate: bool
    accepted: bool


class CheckoutService:
    def __init__(self, settings: Settings, store: OrderStore, provider: PaymentProvider) -> None:
        self.settings = settings
        self.store = store
        self.provider = provider

    async def create(
        self,
        request: CheckoutInput,
        binding: IdempotencyBinding,
        now: datetime,
        random_bytes: bytes,
    ) -> CheckoutOutput:
        if self.settings.payment_mode == PaymentMode.DISABLED:
            raise CheckoutUnavailable("checkout is disabled")
        reserved = self._reserve(request, binding, now, random_bytes)
        if isinstance(reserved, CheckoutOutput):
            return reserved
        result = await self._call_provider(reserved)
        _validate_checkout_url(result.checkout_url)
        return self._finalize(reserved, binding, result, datetime.now(UTC))

    def _reserve(
        self,
        request: CheckoutInput,
        binding: IdempotencyBinding,
        now: datetime,
        random_bytes: bytes,
    ) -> CheckoutPlan | CheckoutOutput:
        endpoint = "POST:/v1/checkout"
        try:
            with self.store.session_factory() as session, session.begin():
                existing = self.store.find_idempotency(session, endpoint, binding.key_sha256)
                if existing is not None:
                    if not hmac_compare(existing.request_hash, binding.request_sha256):
                        raise IdempotencyConflict("idempotency key is bound to another request")
                    return self._resume(session, existing, binding, now)
                order_id = uuid.uuid4()
                order_reference = _new_order_reference(now, random_bytes)
                provider_idempotency_key = binding.key_sha256.hex()
                price_id = self.settings.stripe_price_id or "fixture_price"
                success_url = self.settings.checkout_success_url or "https://example.test/timestamp/status"
                cancel_url = self.settings.checkout_cancel_url or "https://example.test/timestamp/cancelled"
                lease_id = str(uuid.uuid4())
                reservation = IdempotencyRequest(
                    endpoint=endpoint,
                    key_hash=binding.key_sha256,
                    request_hash=binding.request_sha256,
                    order_id=order_id,
                    provider_idempotency_key=provider_idempotency_key,
                    provider_price_id=price_id,
                    success_url=success_url,
                    cancel_url=cancel_url,
                    checkout_state="processing",
                    checkout_lease_id=lease_id,
                    checkout_lease_expires_at=self._lease_expiry(now),
                    response_status=None,
                    response_body=None,
                    completed_at=None,
                    created_at=now,
                )
                order = Order(
                    id=order_id,
                    order_reference=order_reference,
                    certificate_reference=request.certificate_reference,
                    manifest_digest=request.manifest_digest,
                    email=request.email,
                    amount_minor=self.settings.checkout_amount_minor,
                    currency=self.settings.checkout_currency,
                    product_version=self.settings.product_version,
                    payment_mode=self.settings.payment_mode.value,
                    payment_state=PaymentState.CHECKOUT_OPEN.value,
                    fulfillment_state=FulfillmentState.AWAITING_PAYMENT.value,
                    consent_terms_version=request.terms_version,
                    consent_privacy_version=request.privacy_version,
                    consent_accepted_at=request.accepted_at,
                    checkout_session_id=None,
                    payment_intent_id=None,
                    fulfillment_key=f"stamp:{order_id}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(order)
                session.flush()
                session.add(reservation)
                session.flush()
                self.store.record_state_event(
                    session,
                    order,
                    event_key=f"checkout:{order.id}",
                    source="checkout",
                    previous_payment=order.payment_state,
                    previous_fulfillment=order.fulfillment_state,
                    now=now,
                )
                return self._plan(session, reservation)
        except IntegrityError:
            with self.store.session_factory() as session, session.begin():
                existing = self.store.find_idempotency(session, endpoint, binding.key_sha256)
                if existing is None:
                    raise
                if not hmac_compare(existing.request_hash, binding.request_sha256):
                    raise IdempotencyConflict("idempotency key is bound to another request") from None
                return self._resume(session, existing, binding, now)

    async def _call_provider(self, plan: CheckoutPlan) -> HostedCheckoutResult:
        return await self.provider.create_checkout(
            HostedCheckoutRequest(
                internal_order_id=str(plan.order_id),
                order_reference=plan.order_reference,
                customer_email=plan.customer_email,
                price_id=plan.price_id,
                amount_minor=plan.amount_minor,
                currency=plan.currency,
                success_url=plan.success_url,
                cancel_url=plan.cancel_url,
            ),
            plan.provider_idempotency_key,
        )

    def _finalize(
        self,
        plan: CheckoutPlan,
        binding: IdempotencyBinding,
        result: HostedCheckoutResult,
        now: datetime,
    ) -> CheckoutOutput:
        with self.store.session_factory() as session, session.begin():
            reservation = session.scalar(
                select(IdempotencyRequest)
                .where(IdempotencyRequest.id == plan.reservation_id)
                .with_for_update()
            )
            if reservation is None or not hmac_compare(reservation.request_hash, binding.request_sha256):
                raise IdempotencyConflict("idempotent reservation is unavailable")
            if reservation.checkout_state == "completed":
                raise CheckoutInProgress("checkout credential is in its grace period")
            if (
                reservation.checkout_state != "processing"
                or reservation.checkout_lease_id != plan.lease_id
                or reservation.checkout_lease_expires_at is None
                or _aware(reservation.checkout_lease_expires_at) <= _aware(now)
            ):
                raise CheckoutInProgress("checkout processing lease is unavailable")
            order = session.scalar(select(Order).where(Order.id == reservation.order_id).with_for_update())
            if order is None:
                raise IdempotencyConflict("idempotent order is unavailable")
            if order.checkout_session_id not in {None, result.session_id}:
                raise IdempotencyConflict("provider session conflicts with reserved order")
            order.checkout_session_id = result.session_id
            order.updated_at = now
            raw_token = self.store.issue_token(session, order, now, revoke_existing=False)
            safe_response: dict[str, object] = {
                "order_reference": order.order_reference,
                "checkout_url": result.checkout_url,
                "payment_state": order.payment_state,
                "fulfillment_state": order.fulfillment_state,
            }
            reservation.checkout_state = "completed"
            reservation.response_status = 201
            reservation.response_body = safe_response
            reservation.completed_at = now
            reservation.checkout_lease_expires_at = self._lease_expiry(now)
            return CheckoutOutput(status_token=raw_token, **safe_response)  # type: ignore[arg-type]

    def _resume(
        self,
        session: object,
        reservation: IdempotencyRequest,
        binding: IdempotencyBinding,
        now: datetime,
    ) -> CheckoutPlan | CheckoutOutput:
        lease_expires_at = reservation.checkout_lease_expires_at
        if lease_expires_at is not None and _aware(lease_expires_at) > _aware(now):
            raise CheckoutInProgress("checkout credential is in its grace period")
        reservation.checkout_lease_id = str(uuid.uuid4())
        reservation.checkout_lease_expires_at = self._lease_expiry(now)
        if reservation.checkout_state == "completed":
            return self._replay(session, reservation, binding, now)
        reservation.checkout_state = "processing"
        return self._plan(session, reservation)

    def _lease_expiry(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.settings.checkout_credential_grace_seconds)

    def _plan(self, session: object, reservation: IdempotencyRequest) -> CheckoutPlan:
        from sqlalchemy.orm import Session

        if not isinstance(session, Session):
            raise TypeError("invalid database session")
        order = session.get(Order, reservation.order_id)
        if order is None:
            raise IdempotencyConflict("reserved order is unavailable")
        return CheckoutPlan(
            reservation_id=reservation.id,
            order_id=order.id,
            order_reference=order.order_reference,
            customer_email=order.email,
            price_id=reservation.provider_price_id,
            amount_minor=order.amount_minor,
            currency=order.currency,
            success_url=reservation.success_url,
            cancel_url=reservation.cancel_url,
            provider_idempotency_key=reservation.provider_idempotency_key,
            lease_id=str(reservation.checkout_lease_id),
        )

    def _replay(
        self,
        session: object,
        existing: IdempotencyRequest,
        binding: IdempotencyBinding,
        now: datetime,
    ) -> CheckoutOutput:
        from sqlalchemy.orm import Session

        if not isinstance(session, Session):
            raise TypeError("invalid database session")
        if not hmac_compare(existing.request_hash, binding.request_sha256):
            raise IdempotencyConflict("idempotency key is bound to another request")
        if (
            existing.checkout_state != "completed"
            or existing.completed_at is None
            or existing.response_body is None
        ):
            raise IdempotencyConflict("idempotent request is still incomplete")
        order_reference = str(existing.response_body["order_reference"])
        order = session.scalar(select(Order).where(Order.order_reference == order_reference).with_for_update())
        if order is None:
            raise IdempotencyConflict("idempotent order is unavailable")
        raw_token = self.store.issue_token(session, order, now, revoke_existing=True)
        return CheckoutOutput(
            order_reference=order.order_reference,
            status_token=raw_token,
            checkout_url=str(existing.response_body["checkout_url"]),
            payment_state=str(existing.response_body["payment_state"]),
            fulfillment_state=str(existing.response_body["fulfillment_state"]),
        )


class WebhookService:
    def __init__(self, settings: Settings, store: OrderStore, provider: PaymentProvider) -> None:
        self.settings = settings
        self.store = store
        self.provider = provider

    async def process(self, raw_body: bytes, signature: str, now: datetime) -> WebhookResult:
        event = self.provider.verify_event(raw_body, signature, self.settings.stripe_signature_tolerance_seconds)
        payload_hash = hashlib.sha256(raw_body).digest()
        with self.store.session_factory() as duplicate_session:
            duplicate = duplicate_session.scalar(
                select(StripeEvent).where(StripeEvent.stripe_event_id == event.event_id)
            )
            if duplicate is not None:
                return self._duplicate_result(duplicate, event, payload_hash)
        if event.internal_order_id is None:
            internal_order_id = await self.provider.resolve_internal_order_id(event)
            event = replace(event, internal_order_id=internal_order_id)
        with self.store.session_factory() as lookup_session:
            lookup_order = self.store.find_order_for_event(
                lookup_session,
                event.internal_order_id,
                event.payment_intent_id,
                for_update=False,
            )
            checkout_session_id = lookup_order.checkout_session_id if lookup_order is not None else None
        canonical = (
            await self.provider.retrieve_checkout(checkout_session_id)
            if checkout_session_id is not None
            else None
        )
        try:
            with self.store.session_factory() as session, session.begin():
                duplicate = session.scalar(
                    select(StripeEvent).where(StripeEvent.stripe_event_id == event.event_id).with_for_update()
                )
                if duplicate is not None:
                    return self._duplicate_result(duplicate, event, payload_hash)
                recorded = record_stripe_event(
                    session,
                    event.event_id,
                    event.event_type,
                    payload_hash,
                    now,
                    livemode=event.livemode,
                )
                order = self.store.find_order_for_event(
                    session,
                    event.internal_order_id,
                    event.payment_intent_id,
                    for_update=True,
                )
                if order is None or order.checkout_session_id is None or canonical is None:
                    recorded.safe_error_code = "order_binding_invalid"
                    recorded.processed_at = now
                    return WebhookResult(duplicate=False, accepted=False)
                if not self._valid_binding(order, event, canonical):
                    recorded.safe_error_code = "payment_binding_invalid"
                    recorded.processed_at = now
                    return WebhookResult(duplicate=False, accepted=False)
                previous_payment = order.payment_state
                previous_fulfillment = order.fulfillment_state
                changed = self._apply_event(order, event, canonical)
                order.updated_at = now
                if canonical.payment_intent_id and order.payment_intent_id is None:
                    order.payment_intent_id = canonical.payment_intent_id
                if changed:
                    if order.payment_state == PaymentState.PAID.value:
                        order.fulfillment_state = FulfillmentState.QUEUED.value
                        existing_job = session.scalar(
                            select(DurableJob).where(DurableJob.job_key == order.fulfillment_key)
                        )
                        if existing_job is None:
                            enqueue_fulfillment_once(session, order, now)
                            enqueue_outbox_once(session, order, "payment_confirmed", now)
                    self.store.record_state_event(
                        session,
                        order,
                        event_key=f"stripe:{event.event_id}",
                        source="stripe_webhook",
                        previous_payment=previous_payment,
                        previous_fulfillment=previous_fulfillment,
                        now=now,
                    )
                recorded.processed_at = now
                return WebhookResult(duplicate=False, accepted=True)
        except IntegrityError:
            with self.store.session_factory() as session:
                duplicate = session.scalar(select(StripeEvent).where(StripeEvent.stripe_event_id == event.event_id))
                if duplicate is not None:
                    return self._duplicate_result(duplicate, event, payload_hash)
            raise

    @staticmethod
    def _duplicate_result(recorded: StripeEvent, event: ProviderEvent, payload_hash: bytes) -> WebhookResult:
        exact = (
            hmac_compare(recorded.payload_sha256, payload_hash)
            and recorded.event_type == event.event_type
            and recorded.livemode == event.livemode
            and recorded.processed_at is not None
        )
        return WebhookResult(
            duplicate=exact,
            accepted=exact and recorded.safe_error_code is None,
        )

    def _valid_binding(self, order: Order, event: ProviderEvent, canonical: CanonicalCheckout) -> bool:
        expected_price = self.settings.stripe_price_id or "fixture_price"
        base_valid = all(
            (
                not event.livemode,
                not canonical.livemode,
                canonical.mode == "payment",
                canonical.metadata == {"order_id": str(order.id)},
                canonical.session_id == order.checkout_session_id,
                event.session_id in {None, canonical.session_id},
                event.payment_intent_id in {None, canonical.payment_intent_id},
                order.payment_intent_id in {None, canonical.payment_intent_id},
                canonical.price_id == expected_price,
                canonical.amount_total == order.amount_minor,
                canonical.currency == order.currency,
                canonical.quantity == 1,
                order.payment_mode == self.settings.payment_mode.value,
                self.settings.payment_mode in {PaymentMode.FIXTURE, PaymentMode.STRIPE_TEST},
            )
        )
        if not base_valid:
            return False
        complete_events = {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
        }
        if event.event_type in complete_events and canonical.status != "complete":
            return False
        if event.event_type == "checkout.session.expired" and canonical.status != "expired":
            return False
        if event.event_type in {"charge.refunded", "refund.created", "refund.updated"}:
            if not self._authoritative_full_refund(event, canonical):
                return True
        pi_required = event.event_type in {
            "checkout.session.async_payment_succeeded",
            "charge.refunded",
            "refund.created",
            "refund.updated",
            "charge.dispute.created",
            "charge.dispute.updated",
        } or (event.event_type == "checkout.session.completed" and canonical.payment_status == "paid")
        return not pi_required or (
            event.payment_intent_id is not None
            and canonical.payment_intent_id is not None
            and event.payment_intent_id == canonical.payment_intent_id
        )

    def _apply_event(self, order: Order, event: ProviderEvent, canonical: CanonicalCheckout) -> bool:
        if event.event_type == "checkout.session.completed":
            if canonical.payment_status == "paid":
                target = PaymentState.PAID
            elif canonical.payment_status == "unpaid":
                target = PaymentState.PROCESSING
            else:
                return False
        elif event.event_type == "checkout.session.async_payment_succeeded":
            if canonical.payment_status != "paid":
                return False
            target = PaymentState.PAID
        elif event.event_type == "checkout.session.async_payment_failed":
            if canonical.payment_status == "paid":
                return False
            target = PaymentState.FAILED
        elif event.event_type == "checkout.session.expired":
            target = PaymentState.EXPIRED
        elif event.event_type in {"charge.refunded", "refund.created", "refund.updated"}:
            if not self._authoritative_full_refund(event, canonical):
                return False
            target = PaymentState.REFUNDED
        elif event.event_type in {"charge.dispute.created", "charge.dispute.updated"}:
            target = PaymentState.DISPUTED
        else:
            return False
        return self.store.transition_payment(order, target)

    @staticmethod
    def _authoritative_full_refund(event: ProviderEvent, canonical: CanonicalCheckout) -> bool:
        return (
            event.refund_status == "succeeded"
            and event.refunded_amount is not None
            and event.refunded_amount > 0
            and canonical.refund_status == "succeeded"
            and canonical.refunded_amount_total == canonical.amount_total
            and canonical.amount_total > 0
        )


def _validate_checkout_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "checkout.stripe.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise PaymentProviderError("provider returned an unsafe checkout URL")


def _new_order_reference(now: datetime, random_bytes: bytes) -> str:
    if len(random_bytes) < 10:
        raise ValueError("order reference requires at least 80 random bits")
    timestamp_ms = int(now.timestamp() * 1000) & ((1 << 48) - 1)
    value = (timestamp_ms << 80) | int.from_bytes(random_bytes[:10], "big")
    encoded = "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))
    return f"ts_{encoded}"


def hmac_compare(left: bytes, right: bytes) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
