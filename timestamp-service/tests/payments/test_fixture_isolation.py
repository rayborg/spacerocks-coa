from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import AppEnvironment, Settings
from app.main import create_app
from app.payments.gateway import FixturePaymentProvider


def test_fixture_payment_requires_explicit_test_configuration() -> None:
    with pytest.raises(RuntimeError, match="fixture_payment_forbidden_outside_test"):
        FixturePaymentProvider(app_env=AppEnvironment.LOCAL)


def test_fixture_payment_composition_rejects_non_test_application(app_factory: Any) -> None:
    context = app_factory()
    with pytest.raises(RuntimeError, match="fixture_payment_forbidden_outside_test"):
        create_app(
            Settings(_env_file=None, app_env=AppEnvironment.LOCAL),
            payment_provider=context.provider,
        )


def test_main_composes_fixture_payment_only_from_validated_test_settings(app_factory: Any) -> None:
    context = app_factory()
    app = create_app(context.settings, session_factory=context.session_factory)
    assert isinstance(app.state.services.payment_provider, FixturePaymentProvider)


def test_mutable_environment_cannot_enable_fixture_payment_outside_pytest_process() -> None:
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config.settings import AppEnvironment; "
            "from app.payments.gateway import FixturePaymentProvider; "
            "FixturePaymentProvider(app_env=AppEnvironment.TEST)",
        ],
        cwd=service_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "fixture_payment_forbidden_outside_test" in result.stderr
