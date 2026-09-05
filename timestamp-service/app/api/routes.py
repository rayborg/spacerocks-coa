from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Annotated, Never, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import (
    CheckoutPriceResponse,
    CheckoutRequest,
    CheckoutResponse,
    MetbullRecordResponse,
    OrderStatusResponse,
    RotateTokenResponse,
)
from app.db.fulfillment_adapters import ProofMetadata, SqlProofStore
from app.db.models import (
    BitcoinConfirmationObservation,
    Order,
    OutboxMessage,
    ProofBundle,
    ResendWebhookEvent,
)
from app.db.repositories import OrderStore
from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.metbull import MetbullLookup, MetbullNotFound, MetbullNotOfficial, MetbullUnavailable
from app.payments.gateway import PaymentProviderError, PaymentSignatureError
from app.payments.service import (
    CheckoutInProgress,
    CheckoutInput,
    CheckoutService,
    CheckoutUnavailable,
    IdempotencyConflict,
    WebhookService,
)
from app.ports.proof import ProofBundleContext, ProofBundler, ProofState, StoredProof
from app.security.idempotency import bind_idempotency_request
from app.tasks.dispatch import TaskDispatchCoordinator, TaskDispatchUnavailable

router = APIRouter()


@router.get("/v1/meteorites/metbull", response_model=MetbullRecordResponse)
async def metbull_record(
    request: Request,
    code: Annotated[str, Query(pattern=r"^[1-9][0-9]{0,8}$")],
) -> MetbullRecordResponse:
    if list(request.query_params.multi_items()) != [("code", code)]:
        raise HTTPException(status_code=422, detail="invalid request")
    services = request.app.state.services
    lookup: MetbullLookup | None = services.metbull_lookup
    if not services.settings.metbull_lookup_enabled or lookup is None:
        raise HTTPException(status_code=503, detail="meteorite lookup unavailable")
    try:
        record = await lookup.lookup(int(code))
        return MetbullRecordResponse.model_validate(record, from_attributes=True)
    except MetbullNotFound as error:
        raise HTTPException(status_code=404, detail="meteorite record not found") from error
    except MetbullNotOfficial as error:
        raise HTTPException(status_code=409, detail="meteorite name is not official") from error
    except (MetbullUnavailable, ValueError) as error:
        raise HTTPException(status_code=503, detail="meteorite lookup unavailable") from error


@router.get("/v1/checkout/price", response_model=CheckoutPriceResponse)
async def checkout_price(request: Request, response: Response) -> CheckoutPriceResponse:
    services = request.app.state.services
    checkout: CheckoutService | None = services.checkout
    if checkout is None:
        raise HTTPException(status_code=503, detail="checkout unavailable")
    try:
        price = await checkout.get_checkout_price()
    except (CheckoutUnavailable, PaymentProviderError) as error:
        raise HTTPException(status_code=503, detail="checkout unavailable") from error
    response.headers["Cache-Control"] = "no-store"
    return CheckoutPriceResponse(
        amount_minor=price.amount_minor,
        currency=price.currency,
    )


@router.post("/v1/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CheckoutResponse:
    services = request.app.state.services
    checkout: CheckoutService | None = services.checkout
    if checkout is None:
        raise HTTPException(status_code=503, detail="checkout unavailable")
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail="invalid request")
    try:
        binding = bind_idempotency_request(
            idempotency_key,
            "POST",
            "/v1/checkout",
            payload.model_dump(mode="json"),
        )
        result = await checkout.create(
            CheckoutInput(
                certificate_reference=payload.certificate_reference,
                manifest_digest=ManifestDigest.from_hex(payload.manifest_sha256).value,
                email=str(payload.email),
                terms_version=payload.consent.terms_version,
                privacy_version=payload.consent.privacy_version,
                accepted_at=payload.consent.accepted_at,
            ),
            binding,
            datetime.now(UTC),
            secrets.token_bytes(10),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid request") from error
    except IdempotencyConflict as error:
        raise HTTPException(status_code=409, detail="idempotency conflict") from error
    except CheckoutInProgress as error:
        raise HTTPException(status_code=425, detail="checkout processing") from error
    except (CheckoutUnavailable, PaymentProviderError) as error:
        raise HTTPException(status_code=503, detail="checkout unavailable") from error
    return CheckoutResponse.model_validate(result, from_attributes=True)


@router.post("/v1/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> JSONResponse:
    services = request.app.state.services
    webhook: WebhookService | None = services.webhook
    if webhook is None or stripe_signature is None:
        raise HTTPException(status_code=400, detail="invalid webhook")
    raw_body = await request.body()
    try:
        result = await webhook.process(raw_body, stripe_signature, datetime.now(UTC))
    except (PaymentSignatureError, PaymentProviderError, ValueError) as error:
        raise HTTPException(status_code=400, detail="invalid webhook") from error
    if not result.accepted:
        raise HTTPException(status_code=400, detail="invalid webhook")
    task_dispatch: TaskDispatchCoordinator | None = services.task_dispatch
    if task_dispatch is not None:
        try:
            await task_dispatch.reconcile(limit=100)
        except TaskDispatchUnavailable as error:
            raise HTTPException(status_code=503, detail="webhook temporarily unavailable") from error
    return JSONResponse({"received": True, "duplicate": result.duplicate})


@router.get("/v1/orders/status", response_model=OrderStatusResponse, response_model_exclude_none=True)
async def order_status(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> OrderStatusResponse:
    services = request.app.state.services
    store = _store(request)
    raw_token = _bearer_token(authorization)
    with store.session_factory() as session:
        authenticated = store.authenticate(session, raw_token, datetime.now(UTC))
        if authenticated is None:
            _unauthorized()
        order = authenticated.order
        calendar_submitted_at: datetime | None = None
        bitcoin_verified_at: datetime | None = None
        proof_available = False
        proof_states = {"calendar_pending", "bitcoin_verified", "delivered"}
        if order.fulfillment_state in proof_states:
            proof_store: SqlProofStore | None = services.proof_store
            if proof_store is None:
                raise HTTPException(status_code=503, detail="status unavailable")
            try:
                proof = proof_store.latest_for_order(session, order)
            except ValueError as error:
                raise HTTPException(status_code=503, detail="status unavailable") from error
            if proof is None:
                raise HTTPException(status_code=503, detail="status unavailable")
            calendar_submitted_at = proof.calendar_submitted_at
            if order.fulfillment_state == "calendar_pending":
                if proof.proof_state != ProofState.CALENDAR_PENDING or services.proof_bundler is None:
                    raise HTTPException(status_code=503, detail="status unavailable")
                proof_available = True
            else:
                if proof.proof_state != ProofState.BITCOIN_VERIFIED or proof.verification is None:
                    raise HTTPException(status_code=503, detail="status unavailable")
                bitcoin_verified_at = proof.verification.verified_at
                bundle = session.scalar(
                    select(ProofBundle).where(
                        ProofBundle.order_id == order.id,
                        ProofBundle.proof_version == proof.version,
                    )
                )
                proof_available = bundle is not None and _valid_bundle(bundle)
                if order.fulfillment_state == "delivered":
                    sender = session.scalar(
                        select(OutboxMessage).where(
                            OutboxMessage.order_id == order.id,
                            OutboxMessage.message_key
                            == f"bitcoin-confirmed-initial-v{proof.version}-{order.order_reference}",
                        )
                    )
                    if not proof_available or not _valid_delivery_sender(session, sender, order.id, proof.version):
                        raise HTTPException(status_code=503, detail="status unavailable")
        message = _message_code(order.fulfillment_state)
        return OrderStatusResponse(
            order_reference=order.order_reference,
            certificate_reference=order.certificate_reference,
            manifest_sha256=order.manifest_digest.hex(),
            payment_state=order.payment_state,
            fulfillment_state=order.fulfillment_state,
            created_at=_utc(order.created_at),
            updated_at=_utc(order.updated_at),
            calendar_submitted_at=_utc(calendar_submitted_at) if calendar_submitted_at else None,
            bitcoin_verified_at=_utc(bitcoin_verified_at) if bitcoin_verified_at else None,
            proof_available=proof_available,
            message_code=message,
        )


@router.post("/v1/orders/rotate-token", response_model=RotateTokenResponse)
async def rotate_token(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> RotateTokenResponse:
    store = _store(request)
    raw_token = _bearer_token(authorization)
    now = datetime.now(UTC)
    with store.session_factory() as session, session.begin():
        authenticated = store.authenticate(session, raw_token, now, for_update=True)
        if authenticated is None:
            _unauthorized()
        replacement = store.issue_token(session, authenticated.order, now, revoke_existing=True)
    return RotateTokenResponse(status_token=replacement)


@router.get("/v1/orders/proof")
async def proof_bundle(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Response:
    services = request.app.state.services
    store = _store(request)
    raw_token = _bearer_token(authorization)
    proof_store: SqlProofStore | None = services.proof_store
    if proof_store is None:
        raise HTTPException(status_code=503, detail="proof unavailable")
    with store.session_factory() as session, session.begin():
        authenticated = store.authenticate(session, raw_token, datetime.now(UTC), for_update=True)
        if authenticated is None:
            _unauthorized()
        order = session.scalar(select(Order).where(Order.id == authenticated.order.id).with_for_update())
        if order is None:
            _unauthorized()
        if order.fulfillment_state == "manual_review":
            raise HTTPException(status_code=409, detail="proof unavailable")
        if order.fulfillment_state not in {"calendar_pending", "bitcoin_verified", "delivered"}:
            raise HTTPException(status_code=425, detail="proof pending")
        try:
            stored = proof_store.latest_for_order(session, order)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="proof unavailable") from error
        if stored is None:
            raise HTTPException(status_code=425, detail="proof pending")
        order_id = order.id
        order_reference = order.order_reference
        certificate_reference = order.certificate_reference
        manifest_digest = order.manifest_digest
        service_version = services.settings.product_version
        fulfillment_state = order.fulfillment_state
        if fulfillment_state in {"bitcoin_verified", "delivered"}:
            if stored.proof_state != ProofState.BITCOIN_VERIFIED or stored.verification is None:
                raise HTTPException(status_code=409, detail="proof unavailable")
            record = session.scalar(
                select(ProofBundle).where(
                    ProofBundle.order_id == order.id,
                    ProofBundle.proof_version == stored.version,
                )
            )
            if record is None or not _valid_bundle(record):
                raise HTTPException(status_code=409, detail="proof unavailable")
            bundle = record.bundle_bytes
            artifact_sha256 = record.bundle_sha256
            store.record_download_event(
                session,
                order_id,
                stored.version,
                artifact_sha256,
                "persisted_verified",
                datetime.now(UTC),
            )
            return _bundle_response(bundle, order_reference)
        if fulfillment_state != "calendar_pending":
            raise HTTPException(status_code=425, detail="proof unavailable")
        if stored.proof_state != ProofState.CALENDAR_PENDING:
            raise HTTPException(status_code=409, detail="proof unavailable")
        selected = SqlProofStore.latest_metadata(session, order.id)
        if selected is None:
            raise HTTPException(status_code=425, detail="proof pending")
    bundler: ProofBundler | None = services.proof_bundler
    if bundler is None:
        raise HTTPException(status_code=409, detail="proof unavailable")
    receipt = _receipt(
        order_reference,
        certificate_reference,
        manifest_digest,
        stored,
        service_version,
    )
    bundle = await bundler.build(
        stored,
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
        ProofBundleContext(
            certificate_reference=CertificateReference(certificate_reference),
            service_version=service_version,
        ),
    )
    artifact_sha256 = hashlib.sha256(bundle).digest()
    with store.session_factory() as session, session.begin():
        authenticated = store.authenticate(session, raw_token, datetime.now(UTC), for_update=True)
        if authenticated is None:
            _unauthorized()
        order = session.scalar(select(Order).where(Order.id == authenticated.order.id).with_for_update())
        if order is None or not _pending_selection_is_current(
            order,
            selected,
            order_id,
            order_reference,
            certificate_reference,
            manifest_digest,
        ):
            raise HTTPException(status_code=409, detail="proof unavailable")
        try:
            current = SqlProofStore.latest_metadata(session, order.id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="proof unavailable") from error
        if current != selected:
            raise HTTPException(status_code=409, detail="proof unavailable")
        store.record_download_event(
            session,
            order.id,
            stored.version,
            artifact_sha256,
            "generated_pending",
            datetime.now(UTC),
        )
    return _bundle_response(bundle, order_reference)


def _bundle_response(bundle: bytes, order_reference: str) -> Response:
    return Response(
        bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{order_reference}-timestamp.zip"'},
    )


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    services = request.app.state.services
    if services.store is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        with services.store.session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


def _bearer_token(value: str | None) -> str:
    if value is None or not value.startswith("Bearer ") or value.count(" ") != 1:
        _unauthorized()
    return value.removeprefix("Bearer ")


def _unauthorized() -> Never:
    raise HTTPException(status_code=401, detail="not authorized", headers={"WWW-Authenticate": "Bearer"})


def _valid_bundle(record: ProofBundle) -> bool:
    return (
        1 <= record.bundle_byte_length <= 12 * 1024 * 1024
        and record.bundle_byte_length == len(record.bundle_bytes)
        and hashlib.sha256(record.bundle_bytes).digest() == record.bundle_sha256
    )


def _valid_delivery_sender(
    session: Session,
    record: OutboxMessage | None,
    order_id: object,
    proof_version: int,
) -> bool:
    if (
        record is None
        or record.kind != "bitcoin-confirmed-initial"
        or record.state != "delivered"
        or record.order_id != order_id
        or record.proof_version != proof_version
        or record.confirmation_count is None
        or record.confirmation_count < 1
        or record.confirmation_observation_id is None
        or record.provider_message_id is None
        or record.accepted_at is None
        or record.delivered_at is None
    ):
        return False
    observation = session.get(BitcoinConfirmationObservation, record.confirmation_observation_id)
    delivered_event = session.scalar(
        select(ResendWebhookEvent).where(
            ResendWebhookEvent.provider_message_id == record.provider_message_id,
            ResendWebhookEvent.event_type == "email.delivered",
        )
    )
    return bool(
        observation is not None
        and observation.order_id == order_id
        and observation.proof_version == proof_version
        and observation.observed_confirmations == record.confirmation_count
        and delivered_event is not None
    )


def _pending_selection_is_current(
    order: Order,
    selected: ProofMetadata,
    order_id: object,
    order_reference: str,
    certificate_reference: str,
    manifest_digest: bytes,
) -> bool:
    return (
        order.id == order_id
        and order.order_reference == order_reference
        and order.certificate_reference == certificate_reference
        and order.manifest_digest == manifest_digest
        and order.fulfillment_state == "calendar_pending"
        and selected.target_digest == manifest_digest
        and selected.proof_state == "calendar_pending"
    )


def _message_code(fulfillment_state: str) -> str:
    return {
        "awaiting_payment": "payment_pending",
        "queued": "timestamp_queued",
        "stamping": "timestamp_stamping",
        "calendar_pending": "bitcoin_confirmation_pending",
        "bitcoin_verified": "bitcoin_verified",
        "delivered": "proof_delivered",
        "manual_review": "manual_review",
    }.get(fulfillment_state, "status_unavailable")


def _receipt(
    order_reference: str,
    certificate: str,
    digest: bytes,
    proof: StoredProof,
    service_version: str,
) -> dict[str, object]:
    if proof.target_digest.value != digest:
        raise HTTPException(status_code=409, detail="proof unavailable")
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "order_reference": order_reference,
        "certificate_reference": certificate,
        "manifest_sha256": digest.hex(),
        "proof_sha256": proof.proof_sha256.hex(),
        "proof_bytes": proof.proof_byte_length,
        "proof_state": proof.proof_state.value,
        "calendar_submitted_at": proof.calendar_submitted_at.isoformat(),
        "service_version": service_version,
    }
    if proof.proof_state == ProofState.BITCOIN_VERIFIED:
        verification = proof.verification
        if (
            verification is None
            or verification.block_height is None
            or verification.block_hash is None
            or verification.block_time is None
            or verification.confirmation_policy is None
            or verification.verified_at is None
        ):
            raise HTTPException(status_code=409, detail="proof unavailable")
        receipt.update(
            {
                "bitcoin": {
                    "block_height": verification.block_height,
                    "block_hash": verification.block_hash,
                    "block_time": verification.block_time.isoformat(),
                    "confirmation_policy": verification.confirmation_policy,
                },
                "verification_method": verification.method,
                "verified_at": verification.verified_at.isoformat(),
            }
        )
    return receipt


def _order_reference(value: str) -> OrderReference:
    return OrderReference(value)


def _store(request: Request) -> OrderStore:
    store = cast(OrderStore | None, request.app.state.services.store)
    if store is None:
        raise HTTPException(status_code=503, detail="service unavailable")
    return store


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
