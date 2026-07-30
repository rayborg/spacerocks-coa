from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api import router
from app.config.settings import AppEnvironment, PaymentMode, Settings
from app.db.fulfillment_adapters import SqlBundleRepository, SqlProofStore
from app.db.repositories import OrderStore, RateLimitStore
from app.db.session import create_database_engine, create_session_factory
from app.observability.logging import configure_safe_logging
from app.payments.gateway import FixturePaymentProvider, PaymentProvider, StripeTestPaymentProvider
from app.payments.service import CheckoutService, WebhookService
from app.ports.proof import ProofBundler
from app.proofs.factory import create_proof_bundler
from app.security.http import HttpSecurityMiddleware, RateLimitMiddleware, SafeRequestLogMiddleware
from app.security.tokens import TokenHasher


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


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    payment_provider: PaymentProvider | None = None,
    proof_bundler: ProofBundler | None = None,
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
            elif configured.payment_mode == PaymentMode.STRIPE_TEST:
                if configured.stripe_secret_key is None or configured.stripe_webhook_secret is None:
                    raise ValueError("validated Stripe test secrets are unavailable")
                payment_provider = StripeTestPaymentProvider(
                    configured.stripe_secret_key.get_secret_value(),
                    configured.stripe_webhook_secret.get_secret_value(),
                    configured.stripe_api_timeout_seconds,
                )
        if payment_provider is not None:
            checkout = CheckoutService(configured, store, payment_provider)
            webhook = WebhookService(configured, store, payment_provider)
        rate_store = RateLimitStore(session_factory, peppers[configured.active_token_pepper_version])
        app.add_middleware(RateLimitMiddleware, store=rate_store, settings=configured)
    app.state.services = AppServices(
        configured,
        store,
        checkout,
        webhook,
        payment_provider,
        proof_bundler,
        proof_store,
        bundle_repository,
    )
    app.include_router(router)
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
