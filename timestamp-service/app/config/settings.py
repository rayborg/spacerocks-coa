from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class PaymentMode(StrEnum):
    DISABLED = "disabled"
    FIXTURE = "fixture"
    STRIPE_TEST = "stripe_test"
    STRIPE_LIVE = "stripe_live"


class FixtureGate(StrEnum):
    DISABLED = "disabled"
    FIXTURE = "fixture"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    app_env: AppEnvironment = AppEnvironment.LOCAL
    payment_mode: PaymentMode = PaymentMode.DISABLED
    bitcoin_verifier: FixtureGate = FixtureGate.DISABLED
    calendar_mode: FixtureGate = FixtureGate.DISABLED
    allowed_origins: list[str] = Field(default_factory=list)
    database_url: SecretStr | None = None
    token_peppers: dict[int, SecretStr] = Field(default_factory=dict, repr=False)
    active_token_pepper_version: int | None = None
    stripe_test_enabled: bool = False
    stripe_secret_key: SecretStr | None = Field(default=None, repr=False)
    stripe_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    stripe_price_id: str | None = None
    checkout_amount_minor: int = 500
    checkout_currency: str = "usd"
    product_version: str = "phase0"
    checkout_success_url: str | None = None
    checkout_cancel_url: str | None = None
    token_ttl_seconds: int = 30 * 24 * 60 * 60
    checkout_credential_grace_seconds: int = 60
    stripe_signature_tolerance_seconds: int = 300
    stripe_api_timeout_seconds: float = 10.0
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    checkout_rate_limit: int = 10
    webhook_rate_limit: int = 120
    status_rate_limit: int = 60
    proof_rate_limit: int = 20
    rotation_rate_limit: int = 5

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin == "*" or "*" in origin:
                raise ValueError("wildcard origins are forbidden")
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("origins must be absolute HTTP(S) origins without credentials")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("origins cannot include paths, queries, or fragments")
        return origins

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_proxy_ips(cls, addresses: list[str]) -> list[str]:
        for address in addresses:
            ip_address(address)
        return addresses

    @model_validator(mode="after")
    def validate_phase0_gates(self) -> Self:
        if self.payment_mode == PaymentMode.STRIPE_LIVE:
            raise ValueError("stripe_live is not supported in Phase 0")
        fixture_enabled = any(
            (
                self.payment_mode == PaymentMode.FIXTURE,
                self.bitcoin_verifier == FixtureGate.FIXTURE,
                self.calendar_mode == FixtureGate.FIXTURE,
            )
        )
        if fixture_enabled and self.app_env != AppEnvironment.TEST:
            raise ValueError("fixture modes are allowed only when APP_ENV=test")
        if self.app_env in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
            for origin in self.allowed_origins:
                if not origin.startswith("https://"):
                    raise ValueError("staging and production origins must use HTTPS")
            if self.database_url is None:
                raise ValueError("database_url is required outside local/test environments")
        if self.database_url is not None:
            database_url = self.database_url.get_secret_value()
            remote_environment = self.app_env in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
            if remote_environment and not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("staging and production require PostgreSQL")
        for version, pepper in self.token_peppers.items():
            if version < 1 or len(pepper.get_secret_value().encode("utf-8")) < 32:
                raise ValueError("token peppers require a positive version and at least 32 bytes")
        service_enabled = self.payment_mode != PaymentMode.DISABLED
        if service_enabled:
            if self.database_url is None:
                raise ValueError("database_url is required when payments are enabled")
            if self.active_token_pepper_version not in self.token_peppers:
                raise ValueError("an active strong token pepper is required when payments are enabled")
            if self.checkout_amount_minor <= 0:
                raise ValueError("checkout amount must be positive")
            if (
                len(self.checkout_currency) != 3
                or self.checkout_currency.lower() != self.checkout_currency
                or not self.checkout_currency.isalpha()
            ):
                raise ValueError("checkout currency must be a lowercase three-letter code")
            if not 300 <= self.token_ttl_seconds <= 90 * 24 * 60 * 60:
                raise ValueError("token TTL must be between five minutes and 90 days")
            if not 1 <= len(self.product_version) <= 64:
                raise ValueError("product version must contain 1 through 64 characters")
        if self.active_token_pepper_version is not None and self.active_token_pepper_version not in self.token_peppers:
            raise ValueError("active token pepper version is not configured")
        if self.payment_mode == PaymentMode.STRIPE_TEST:
            if self.app_env not in {AppEnvironment.TEST, AppEnvironment.STAGING} or not self.stripe_test_enabled:
                raise ValueError("stripe_test requires an explicit test gate in test or staging")
            key = self.stripe_secret_key.get_secret_value() if self.stripe_secret_key else ""
            webhook = self.stripe_webhook_secret.get_secret_value() if self.stripe_webhook_secret else ""
            valid_stripe_test_config = (
                key.startswith("sk_test_")
                and webhook.startswith("whsec_")
                and (self.stripe_price_id or "").startswith("price_")
            )
            if not valid_stripe_test_config:
                raise ValueError("stripe_test requires test key, webhook secret, and server-controlled price")
            if not self.checkout_success_url or not self.checkout_cancel_url:
                raise ValueError("stripe_test requires server-controlled success and cancel URLs")
            for checkout_url in (self.checkout_success_url, self.checkout_cancel_url):
                if not checkout_url.startswith("https://"):
                    raise ValueError("Stripe return URLs must use HTTPS")
                parsed = urlsplit(checkout_url)
                checkout_origin = f"{parsed.scheme}://{parsed.netloc}"
                if checkout_origin not in self.allowed_origins:
                    raise ValueError("Stripe return URL origins must be explicitly allowed")
        elif any((self.stripe_secret_key, self.stripe_webhook_secret, self.stripe_price_id, self.stripe_test_enabled)):
            raise ValueError("Stripe configuration is forbidden unless payment_mode=stripe_test")
        limits = (
            self.checkout_rate_limit,
            self.webhook_rate_limit,
            self.status_rate_limit,
            self.proof_rate_limit,
            self.rotation_rate_limit,
        )
        if any(limit < 1 for limit in limits):
            raise ValueError("rate limits must be positive")
        if not 60 <= self.stripe_signature_tolerance_seconds <= 600:
            raise ValueError("Stripe signature tolerance must be between 60 and 600 seconds")
        if not 5 <= self.checkout_credential_grace_seconds <= 300:
            raise ValueError("checkout credential grace must be between 5 and 300 seconds")
        if not 1.0 <= self.stripe_api_timeout_seconds <= 30.0:
            raise ValueError("Stripe API timeout must be between 1 and 30 seconds")
        return self
