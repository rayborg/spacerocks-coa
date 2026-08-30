from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from app.config.settings import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.payments.gateway import FixturePaymentProvider  # noqa: E402
from app.ports.proof import ProofBundler  # noqa: E402


@pytest.fixture(autouse=True)
def deny_ws1_external_sockets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    test_path = request.node.path.as_posix()
    if "/tests/api/" not in test_path and "/tests/payments/" not in test_path:
        return

    def fail_connect(_socket: socket.socket, _address: object) -> None:
        raise AssertionError("WS1 tests must not open network sockets")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)


@dataclass(slots=True)
class ServiceContext:
    app: FastAPI
    settings: Settings
    provider: FixturePaymentProvider
    session_factory: sessionmaker[Session]
    engine: Engine


@pytest.fixture
def app_factory() -> Any:
    contexts: list[ServiceContext] = []

    def build(
        *,
        proof_bundler: ProofBundler | None = None,
        sqlite_url: str = "sqlite://",
        **overrides: object,
    ) -> ServiceContext:
        values: dict[str, object] = {
            "app_env": "test",
            "payment_mode": "fixture",
            "checkout_enabled": True,
            "database_url": sqlite_url,
            "allowed_origins": ["https://coa.example.test"],
            "token_peppers": {1: SecretStr("test-pepper-value-that-is-32-bytes-long")},
            "active_token_pepper_version": 1,
        }
        values.update(overrides)
        settings = Settings(_env_file=None, **values)
        if sqlite_url == "sqlite://":
            engine = create_engine(
                sqlite_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        elif sqlite_url.startswith("sqlite:"):
            engine = create_engine(sqlite_url, connect_args={"check_same_thread": False, "timeout": 10})
        else:
            engine = create_engine(sqlite_url)
        Base.metadata.create_all(engine)
        session_factory = create_session_factory(engine)
        provider = FixturePaymentProvider(app_env=settings.app_env)
        app = create_app(
            settings,
            session_factory=session_factory,
            payment_provider=provider,
            proof_bundler=proof_bundler,
        )
        context = ServiceContext(app, settings, provider, session_factory, engine)
        contexts.append(context)
        return context

    yield build
    for context in contexts:
        context.engine.dispose()
