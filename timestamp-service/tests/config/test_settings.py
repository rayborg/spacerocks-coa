from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import (
    AppEnvironment,
    BitcoinMode,
    CalendarMode,
    PaymentMode,
    ResendSenderMode,
    ResendWebhookMode,
    Settings,
)


def test_defaults_are_inert_and_secret_free() -> None:
    settings = Settings(_env_file=None)
    assert settings.payment_mode == PaymentMode.DISABLED
    assert settings.checkout_enabled is False
    assert settings.bitcoin_verifier == BitcoinMode.DISABLED
    assert settings.calendar_mode == CalendarMode.DISABLED
    assert settings.resend_sender_mode == ResendSenderMode.DISABLED
    assert settings.resend_webhook_mode == ResendWebhookMode.DISABLED
    assert settings.database_url is None
    assert settings.product_version == "phase0"
    assert settings.expected_terms_version == "phase0-v1"
    assert settings.expected_privacy_version == "phase0-v1"


def test_app_env_uses_required_unprefixed_environment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    assert Settings(_env_file=None).app_env == AppEnvironment.TEST


@pytest.mark.parametrize(
    "values",
    [
        {"payment_mode": "stripe_live"},
        {"checkout_enabled": True},
        {"stripe_automatic_tax_enabled": True},
        {"payment_mode": "fixture", "app_env": "local"},
        {"bitcoin_verifier": "fixture", "app_env": "staging", "database_url": "postgresql://db/test"},
        {"calendar_mode": "fixture", "app_env": "production", "database_url": "postgresql://db/test"},
        {"allowed_origins": ["*"]},
        {"allowed_origins": ["https://*.example.test"]},
        {"app_env": "production", "allowed_origins": ["http://example.test"], "database_url": "postgresql://db/test"},
        {"app_env": "staging", "allowed_origins": ["https://example.test"]},
        {"token_peppers": {1: SecretStr("weak")}},
        {"active_token_pepper_version": 2, "token_peppers": {1: SecretStr("x" * 32)}},
        {"stripe_signature_tolerance_seconds": 3600},
        {"checkout_credential_grace_seconds": 1},
        {"checkout_credential_grace_seconds": 301},
        {
            "app_env": "staging",
            "payment_mode": "stripe_test",
            "database_url": "postgresql://db/test",
            "allowed_origins": ["https://example.test"],
            "token_peppers": {1: SecretStr("x" * 32)},
            "active_token_pepper_version": 1,
        },
        {"app_env": "test", "calendar_mode": "public", "calendar_allowlist": ["https://a.test", "https://b.test"]},
        {"app_env": "test", "bitcoin_verifier": "bitcoin_core"},
        {"resend_sender_mode": "resend", "database_url": "sqlite://"},
        {"resend_webhook_mode": "resend", "database_url": "sqlite://"},
        {
            "app_env": "test",
            "payment_mode": "fixture",
            "database_url": "sqlite://",
            "token_peppers": {1: SecretStr("x" * 32)},
            "active_token_pepper_version": 1,
            "checkout_currency": "isk",
        },
        {
            "app_env": "test",
            "payment_mode": "fixture",
            "database_url": "sqlite://",
            "token_peppers": {1: SecretStr("x" * 32)},
            "active_token_pepper_version": 1,
            "checkout_amount_minor": 100_000_001,
        },
    ],
)
def test_unsafe_combinations_fail_closed(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_explicit_stripe_test_configuration_is_accepted_without_secret_repr() -> None:
    settings = Settings(
        _env_file=None,
        app_env=AppEnvironment.STAGING,
        payment_mode=PaymentMode.STRIPE_TEST,
        database_url="postgresql+psycopg://user:password@db/test",
        allowed_origins=["https://coa.example.test"],
        token_peppers={1: SecretStr("p" * 32)},
        active_token_pepper_version=1,
        stripe_test_enabled=True,
        stripe_secret_key="rk_test_do_not_use",
        stripe_webhook_secret="whsec_do_not_use",
        stripe_price_id="price_test",
        checkout_success_url="https://coa.example.test/timestamp/status",
        checkout_cancel_url="https://coa.example.test/timestamp/cancelled",
    )
    rendered = repr(settings)
    assert "password" not in rendered
    assert "rk_test_do_not_use" not in rendered
    assert "whsec_do_not_use" not in rendered
    assert "p" * 32 not in rendered


def test_production_rejects_non_postgres_database() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="sqlite:///unsafe.db",
            allowed_origins=["https://coa.example.test"],
        )


def test_explicit_stripe_live_public_calendar_and_bitcoin_core_configuration_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        payment_mode="stripe_live",
        stripe_live_enabled=True,
        stripe_secret_key="rk_live_do_not_use",
        stripe_webhook_secret="whsec_do_not_use",
        stripe_price_id="price_live",
        checkout_success_url="https://coa.example.test/timestamp/status",
        checkout_cancel_url="https://coa.example.test/timestamp/cancelled",
        database_url="postgresql://user:password@db/test",
        allowed_origins=["https://coa.example.test"],
        token_peppers={1: SecretStr("p" * 32)},
        active_token_pepper_version=1,
        calendar_mode="public",
        calendar_allowlist=["https://calendar-a.example/", "https://calendar-b.example/"],
        bitcoin_verifier="bitcoin_core",
        bitcoin_rpc_url="http://bitcoin.internal:8332/",
        bitcoin_rpc_username="rpc-user",
        bitcoin_rpc_password="rpc-password",
        product_version="managed-timestamp-v1",
        expected_terms_version="2026-08-v1",
        expected_privacy_version="2026-08-v1",
    )
    assert settings.payment_mode == PaymentMode.STRIPE_LIVE
    assert settings.checkout_enabled is False
    assert settings.calendar_mode == CalendarMode.PUBLIC
    assert settings.bitcoin_verifier == BitcoinMode.BITCOIN_CORE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_version", "phase0-production"),
        ("expected_terms_version", "phase0-v2"),
        ("expected_privacy_version", "PHASE0-v2"),
    ],
)
def test_production_rejects_phase0_product_and_policy_versions(field: str, value: str) -> None:
    values: dict[str, object] = {
        "app_env": "production",
        "database_url": "postgresql://user:password@db/test",
        "allowed_origins": ["https://coa.example.test"],
        "product_version": "managed-timestamp-v1",
        "expected_terms_version": "2026-08-v1",
        "expected_privacy_version": "2026-08-v1",
        field: value,
    }
    with pytest.raises(ValidationError, match="phase0"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("field", ["expected_terms_version", "expected_privacy_version"])
def test_expected_policy_versions_are_bounded_safe_identifiers(field: str) -> None:
    with pytest.raises(ValidationError, match="policy versions"):
        Settings(_env_file=None, **{field: "unsafe policy/version"})


def test_resend_sender_and_webhook_secrets_are_independently_required() -> None:
    webhook_only = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        resend_webhook_mode="resend",
        resend_webhook_secret="whsec_cmVzZW5kLXRlc3Qtc2lnbmluZy1zZWNyZXQ=",
    )
    assert webhook_only.resend_api_key is None
    sender_only = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        resend_sender_mode="resend",
        resend_api_key="re_do_not_use",
        resend_sender="proofs@example.test",
    )
    assert sender_only.resend_webhook_secret is None
