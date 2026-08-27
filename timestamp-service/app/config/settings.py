from __future__ import annotations

import base64
import binascii
import re
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


class CalendarMode(StrEnum):
    DISABLED = "disabled"
    FIXTURE = "fixture"
    PUBLIC = "public"


class BitcoinMode(StrEnum):
    DISABLED = "disabled"
    FIXTURE = "fixture"
    BITCOIN_CORE = "bitcoin_core"


class ResendSenderMode(StrEnum):
    DISABLED = "disabled"
    RESEND = "resend"


class ResendWebhookMode(StrEnum):
    DISABLED = "disabled"
    RESEND = "resend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    app_env: AppEnvironment = AppEnvironment.LOCAL
    payment_mode: PaymentMode = PaymentMode.DISABLED
    bitcoin_verifier: BitcoinMode = BitcoinMode.DISABLED
    calendar_mode: CalendarMode = CalendarMode.DISABLED
    resend_sender_mode: ResendSenderMode = ResendSenderMode.DISABLED
    resend_webhook_mode: ResendWebhookMode = ResendWebhookMode.DISABLED
    allowed_origins: list[str] = Field(default_factory=list)
    database_url: SecretStr | None = None
    token_peppers: dict[int, SecretStr] = Field(default_factory=dict, repr=False)
    active_token_pepper_version: int | None = None
    stripe_test_enabled: bool = False
    stripe_live_enabled: bool = False
    stripe_secret_key: SecretStr | None = Field(default=None, repr=False)
    stripe_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    stripe_price_id: str | None = None
    checkout_amount_minor: int = 500
    checkout_currency: str = "usd"
    product_version: str = "phase0"
    expected_terms_version: str = "phase0-v1"
    expected_privacy_version: str = "phase0-v1"
    checkout_success_url: str | None = None
    checkout_cancel_url: str | None = None
    token_ttl_seconds: int = 30 * 24 * 60 * 60
    checkout_credential_grace_seconds: int = 60
    stripe_signature_tolerance_seconds: int = 300
    stripe_api_timeout_seconds: float = 10.0
    stripe_automatic_tax_enabled: bool = False
    calendar_allowlist: list[str] = Field(default_factory=list)
    calendar_timeout_seconds: float = 5.0
    bitcoin_rpc_url: str | None = None
    bitcoin_rpc_username: SecretStr | None = Field(default=None, repr=False)
    bitcoin_rpc_password: SecretStr | None = Field(default=None, repr=False)
    bitcoin_rpc_timeout_seconds: float = 5.0
    resend_api_key: SecretStr | None = Field(default=None, repr=False)
    resend_sender: str | None = None
    resend_api_timeout_seconds: float = 10.0
    resend_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    resend_webhook_tolerance_seconds: int = 300
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    checkout_rate_limit: int = 10
    webhook_rate_limit: int = 120
    resend_webhook_rate_limit: int = 120
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

    @field_validator("product_version")
    @classmethod
    def validate_product_version(cls, version: str) -> str:
        if not 1 <= len(version) <= 64:
            raise ValueError("product version must contain 1 through 64 characters")
        return version

    @field_validator("expected_terms_version", "expected_privacy_version")
    @classmethod
    def validate_policy_version(cls, version: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9._-]{1,32}", version) is None:
            raise ValueError("expected policy versions must contain 1 through 32 safe characters")
        return version

    @model_validator(mode="after")
    def validate_runtime_gates(self) -> Self:
        fixture_enabled = any(
            (
                self.payment_mode == PaymentMode.FIXTURE,
                self.bitcoin_verifier == BitcoinMode.FIXTURE,
                self.calendar_mode == CalendarMode.FIXTURE,
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
        if self.app_env == AppEnvironment.PRODUCTION:
            production_versions = (
                self.product_version,
                self.expected_terms_version,
                self.expected_privacy_version,
            )
            if any(version.casefold().startswith("phase0") for version in production_versions):
                raise ValueError("production product and policy versions cannot use phase0 values")
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
        if self.active_token_pepper_version is not None and self.active_token_pepper_version not in self.token_peppers:
            raise ValueError("active token pepper version is not configured")
        if self.stripe_automatic_tax_enabled:
            raise ValueError("Stripe automatic tax is hard-disabled")
        if self.payment_mode in {PaymentMode.STRIPE_TEST, PaymentMode.STRIPE_LIVE}:
            live = self.payment_mode == PaymentMode.STRIPE_LIVE
            if live and (self.app_env != AppEnvironment.PRODUCTION or not self.stripe_live_enabled):
                raise ValueError("stripe_live requires an explicit live gate in production")
            if not live and (
                self.app_env not in {AppEnvironment.TEST, AppEnvironment.STAGING} or not self.stripe_test_enabled
            ):
                raise ValueError("stripe_test requires an explicit test gate in test or staging")
            key = self.stripe_secret_key.get_secret_value() if self.stripe_secret_key else ""
            webhook = self.stripe_webhook_secret.get_secret_value() if self.stripe_webhook_secret else ""
            key_mode = "live" if live else "test"
            valid_stripe_config = (
                key.startswith((f"sk_{key_mode}_", f"rk_{key_mode}_"))
                and webhook.startswith("whsec_")
                and (self.stripe_price_id or "").startswith("price_")
            )
            if not valid_stripe_config:
                raise ValueError("Stripe mode requires a matching key, webhook secret, and server-controlled price")
            if not self.checkout_success_url or not self.checkout_cancel_url:
                raise ValueError("Stripe mode requires server-controlled success and cancel URLs")
            for checkout_url in (self.checkout_success_url, self.checkout_cancel_url):
                if not checkout_url.startswith("https://"):
                    raise ValueError("Stripe return URLs must use HTTPS")
                parsed = urlsplit(checkout_url)
                checkout_origin = f"{parsed.scheme}://{parsed.netloc}"
                if checkout_origin not in self.allowed_origins:
                    raise ValueError("Stripe return URL origins must be explicitly allowed")
            if live and self.stripe_test_enabled:
                raise ValueError("Stripe test and live gates cannot both be enabled")
            if not live and self.stripe_live_enabled:
                raise ValueError("Stripe test and live gates cannot both be enabled")
        elif any(
            (
                self.stripe_secret_key,
                self.stripe_webhook_secret,
                self.stripe_price_id,
                self.stripe_test_enabled,
                self.stripe_live_enabled,
            )
        ):
            raise ValueError("Stripe configuration is forbidden unless a Stripe payment mode is enabled")

        remote_environment = self.app_env in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
        if self.calendar_mode == CalendarMode.PUBLIC:
            if not remote_environment:
                raise ValueError("public calendars require staging or production")
            from app.timestamping.calendars import CalendarConfiguration

            CalendarConfiguration(
                allowlist=tuple(self.calendar_allowlist),
                enabled=True,
                timeout_seconds=self.calendar_timeout_seconds,
            ).validated_urls()
        elif self.calendar_allowlist:
            raise ValueError("calendar allowlist is forbidden unless public calendars are enabled")

        if self.bitcoin_verifier == BitcoinMode.BITCOIN_CORE:
            if not remote_environment:
                raise ValueError("Bitcoin Core requires staging or production")
            username = self.bitcoin_rpc_username.get_secret_value() if self.bitcoin_rpc_username else ""
            password = self.bitcoin_rpc_password.get_secret_value() if self.bitcoin_rpc_password else ""
            if self.bitcoin_rpc_url is None or not username or not password:
                raise ValueError("Bitcoin Core requires explicit RPC URL and credentials")
            from app.bitcoin.rpc import BitcoinCoreRpcTransport

            BitcoinCoreRpcTransport(
                self.bitcoin_rpc_url,
                username,
                password,
                timeout_seconds=self.bitcoin_rpc_timeout_seconds,
            )
        elif any((self.bitcoin_rpc_url, self.bitcoin_rpc_username, self.bitcoin_rpc_password)):
            raise ValueError("Bitcoin RPC configuration is forbidden unless Bitcoin Core is enabled")

        if self.resend_sender_mode == ResendSenderMode.RESEND:
            api_key = self.resend_api_key.get_secret_value() if self.resend_api_key else ""
            if self.database_url is None or not api_key or self.resend_sender is None:
                raise ValueError("Resend sender mode requires database, API key, and sender")
            from app.notifications.resend import ResendEmailSender

            ResendEmailSender(api_key, self.resend_sender, timeout_seconds=self.resend_api_timeout_seconds)
        elif any((self.resend_api_key, self.resend_sender)):
            raise ValueError("Resend sender configuration is forbidden unless sender mode is enabled")

        if self.resend_webhook_mode == ResendWebhookMode.RESEND:
            webhook_secret = self.resend_webhook_secret.get_secret_value() if self.resend_webhook_secret else ""
            if self.database_url is None or not webhook_secret:
                raise ValueError("Resend webhook mode requires database and webhook secret")
            try:
                decoded_webhook_secret = base64.b64decode(
                    webhook_secret.removeprefix("whsec_"),
                    validate=True,
                )
            except (ValueError, binascii.Error) as error:
                raise ValueError("Resend webhook secret is invalid") from error
            if not webhook_secret.startswith("whsec_") or len(decoded_webhook_secret) < 16:
                raise ValueError("Resend webhook secret is invalid")
        elif self.resend_webhook_secret is not None:
            raise ValueError("Resend webhook secret is forbidden unless webhook mode is enabled")
        limits = (
            self.checkout_rate_limit,
            self.webhook_rate_limit,
            self.resend_webhook_rate_limit,
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
        if not 0.1 <= self.calendar_timeout_seconds <= 30.0:
            raise ValueError("calendar timeout must be between 0.1 and 30 seconds")
        if not 0.1 <= self.bitcoin_rpc_timeout_seconds <= 30.0:
            raise ValueError("Bitcoin RPC timeout must be between 0.1 and 30 seconds")
        if not 0.1 <= self.resend_api_timeout_seconds <= 30.0:
            raise ValueError("Resend API timeout must be between 0.1 and 30 seconds")
        if not 1 <= self.resend_webhook_tolerance_seconds <= 900:
            raise ValueError("Resend webhook tolerance must be between 1 and 900 seconds")
        return self
