from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api import router
from app.config.settings import AppEnvironment, PaymentMode, ResendWebhookMode, Settings
from app.db.fulfillment_adapters import SqlBundleRepository, SqlProofStore
from app.db.notification_adapters import SqlResendWebhookStore
from app.db.repositories import OrderStore, RateLimitStore
from app.db.session import create_database_engine, create_session_factory
from app.metbull import MetbullLookup, MeteoriticalBulletinClient
from app.notifications.routes import router as notification_router
from app.notifications.webhooks import ResendWebhookService
from app.observability.logging import configure_safe_logging
from app.payments.gateway import FixturePaymentProvider, PaymentProvider, StripePaymentProvider
from app.payments.service import CheckoutService, WebhookService
from app.ports.proof import ProofBundler
from app.proofs.factory import create_proof_bundler
from app.security.http import HttpSecurityMiddleware, RateLimitMiddleware, SafeRequestLogMiddleware
from app.security.tokens import TokenHasher
from app.tasks.composition import create_task_dispatch
from app.tasks.dispatch import TaskDispatchCoordinator


@dataclass(slots=True)
class AppServices:
    settings: Settings
    store: OrderStore | None
    checkout: CheckoutService | None
    webhook: WebhookService | None
    payment_provider: PaymentProvider | None
    proof_bundler: ProofBundler | None
    proof_store: SqlProofStore | None
    bundle_repository: SqlBundleRepository | None
    resend_webhooks: ResendWebhookService | None
    task_dispatch: TaskDispatchCoordinator | None
    metbull_lookup: MetbullLookup | None


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    payment_provider: PaymentProvider | None = None,
    proof_bundler: ProofBundler | None = None,
    metbull_lookup: MetbullLookup | None = None,
) -> FastAPI:
    configured = settings or Settings()
    if isinstance(payment_provider, FixturePaymentProvider) and configured.app_env != AppEnvironment.TEST:
        raise RuntimeError("fixture_payment_forbidden_outside_test")
    docs_enabled = configured.app_env in {AppEnvironment.LOCAL, AppEnvironment.TEST}
    app = FastAPI(
        title="Spacerocks Timestamp Service",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    if session_factory is None and configured.database_url is not None:
        engine = create_database_engine(configured.database_url.get_secret_value())
        session_factory = create_session_factory(engine)
    store: OrderStore | None = None
    checkout: CheckoutService | None = None
    webhook: WebhookService | None = None
    proof_store = SqlProofStore(session_factory) if session_factory is not None else None
    bundle_repository = SqlBundleRepository(session_factory) if session_factory is not None else None
    resend_webhooks: ResendWebhookService | None = None
    task_dispatch = create_task_dispatch(configured, session_factory) if session_factory is not None else None
    if configured.metbull_lookup_enabled and metbull_lookup is None:
        metbull_lookup = MeteoriticalBulletinClient(configured.metbull_timeout_seconds)
    if not configured.metbull_lookup_enabled:
        metbull_lookup = None
    if session_factory is not None and configured.active_token_pepper_version is not None:
        peppers = {version: secret.get_secret_value().encode() for version, secret in configured.token_peppers.items()}
        token_hasher = TokenHasher(peppers)
        store = OrderStore(
            session_factory,
            token_hasher,
            configured.active_token_pepper_version,
            timedelta(seconds=configured.token_ttl_seconds),
        )
        if payment_provider is None:
            if configured.payment_mode == PaymentMode.FIXTURE:
                payment_provider = FixturePaymentProvider(app_env=configured.app_env)
            elif configured.payment_mode in {PaymentMode.STRIPE_TEST, PaymentMode.STRIPE_LIVE}:
                if configured.stripe_secret_key is None or configured.stripe_webhook_secret is None:
                    raise ValueError("validated Stripe secrets are unavailable")
                payment_provider = StripePaymentProvider(
                    configured.stripe_secret_key.get_secret_value(),
                    configured.stripe_webhook_secret.get_secret_value(),
                    payment_mode=configured.payment_mode,
                    timeout_seconds=configured.stripe_api_timeout_seconds,
                )
        if payment_provider is not None:
            checkout = CheckoutService(configured, store, payment_provider)
            webhook = WebhookService(configured, store, payment_provider)
    if session_factory is not None and configured.resend_webhook_mode == ResendWebhookMode.RESEND:
        if configured.resend_webhook_secret is None:
            raise ValueError("validated Resend webhook secret is unavailable")
        resend_secret = configured.resend_webhook_secret.get_secret_value()
        resend_webhooks = ResendWebhookService(
            resend_secret,
            SqlResendWebhookStore(session_factory),
            tolerance_seconds=configured.resend_webhook_tolerance_seconds,
        )
    if session_factory is not None:
        rate_limit_secret: bytes | None = None
        if configured.active_token_pepper_version is not None:
            rate_limit_secret = (
                configured.token_peppers[configured.active_token_pepper_version].get_secret_value().encode()
            )
        elif configured.resend_webhook_mode == ResendWebhookMode.RESEND:
            assert configured.resend_webhook_secret is not None
            rate_limit_secret = hashlib.sha256(configured.resend_webhook_secret.get_secret_value().encode()).digest()
        elif configured.metbull_lookup_enabled and configured.database_url is not None:
            rate_limit_secret = hashlib.sha256(
                b"metbull-rate-limit\0" + configured.database_url.get_secret_value().encode()
            ).digest()
        if rate_limit_secret is not None:
            app.add_middleware(
                RateLimitMiddleware,
                store=RateLimitStore(session_factory, rate_limit_secret),
                settings=configured,
            )
    app.state.services = AppServices(
        settings=configured,
        store=store,
        checkout=checkout,
        webhook=webhook,
        payment_provider=payment_provider,
        proof_bundler=proof_bundler,
        proof_store=proof_store,
        bundle_repository=bundle_repository,
        resend_webhooks=resend_webhooks,
        task_dispatch=task_dispatch,
        metbull_lookup=metbull_lookup,
    )
    app.include_router(router)
    if resend_webhooks is not None:
        app.include_router(notification_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Cache-Control",
            "Content-Type",
            "Idempotency-Key",
            "Stripe-Signature",
        ],
        expose_headers=["Content-Disposition", "Content-Length", "Cache-Control"],
    )
    app.add_middleware(HttpSecurityMiddleware)
    app.add_middleware(SafeRequestLogMiddleware, logger=configure_safe_logging())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    return app


app = create_app(proof_bundler=create_proof_bundler())
