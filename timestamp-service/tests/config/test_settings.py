from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import AppEnvironment, FixtureGate, PaymentMode, Settings


def test_defaults_are_inert_and_secret_free() -> None:
    settings = Settings(_env_file=None)
    assert settings.payment_mode == PaymentMode.DISABLED
    assert settings.bitcoin_verifier == FixtureGate.DISABLED
    assert settings.calendar_mode == FixtureGate.DISABLED
    assert settings.database_url is None


def test_app_env_uses_required_unprefixed_environment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    assert Settings(_env_file=None).app_env == AppEnvironment.TEST


@pytest.mark.parametrize(
    "values",
    [
        {"payment_mode": "stripe_live"},
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
        stripe_secret_key="sk_test_do_not_use",
        stripe_webhook_secret="whsec_do_not_use",
        stripe_price_id="price_test",
        checkout_success_url="https://coa.example.test/timestamp/status",
        checkout_cancel_url="https://coa.example.test/timestamp/cancelled",
    )
    rendered = repr(settings)
    assert "password" not in rendered
    assert "sk_test_do_not_use" not in rendered
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
