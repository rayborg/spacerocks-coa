from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.db.base import Base
from app.db.session import create_session_factory
from app.main import create_app

SECRET_BYTES = b"resend-test-signing-secret-32-bytes"
SECRET = "whsec_" + base64.b64encode(SECRET_BYTES).decode("ascii")


def _signature(raw_body: bytes, event_id: str, timestamp: int) -> str:
    signed = f"{event_id}.{timestamp}.".encode("ascii") + raw_body
    value = base64.b64encode(hmac.new(SECRET_BYTES, signed, hashlib.sha256).digest()).decode("ascii")
    return f"v1,{value}"


def test_composed_resend_route_has_body_and_rate_limits_without_sender_secrets() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        resend_webhook_mode="resend",
        resend_webhook_secret=SECRET,
        resend_webhook_rate_limit=1,
    )
    app = create_app(settings, session_factory=create_session_factory(engine))
    body = b'{"type":"email.sent","created_at":"2026-08-27T12:00:00Z","data":{"email_id":"provider-1"}}'
    event_id = "msg_composed"
    timestamp = int(time.time())
    headers = {
        "svix-id": event_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": _signature(body, event_id, timestamp),
        "content-type": "application/json",
    }
    with TestClient(app) as client:
        accepted = client.post("/v1/webhooks/resend", content=body, headers=headers)
        limited = client.post("/v1/webhooks/resend", content=body, headers=headers)
        oversized = client.post(
            "/v1/webhooks/resend",
            content=b"x" * (256 * 1024 + 1),
            headers={"content-type": "application/json"},
        )
    assert accepted.status_code == 200
    assert limited.status_code == 429
    assert oversized.status_code == 413
    assert settings.resend_api_key is None and settings.resend_sender is None
    engine.dispose()
