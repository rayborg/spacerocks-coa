from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.notifications.routes import router
from app.notifications.webhooks import ResendWebhookService
from app.ports.notifications import WebhookProcessResult

SECRET_BYTES = b"resend-test-signing-secret-32-bytes"
SECRET = "whsec_" + base64.b64encode(SECRET_BYTES).decode("ascii")


class RecordingStore:
    def __init__(self) -> None:
        self.calls = []

    async def process(self, event, payload_sha256, now):
        self.calls.append((event, payload_sha256, now))
        return WebhookProcessResult(duplicate=False)


def signature(raw_body: bytes, event_id: str, timestamp: int) -> str:
    signed = f"{event_id}.{timestamp}.".encode("ascii") + raw_body
    value = base64.b64encode(hmac.new(SECRET_BYTES, signed, hashlib.sha256).digest()).decode("ascii")
    return f"v1,{value}"


def test_resend_route_verifies_svix_signature_over_raw_body() -> None:
    store = RecordingStore()
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(resend_webhooks=ResendWebhookService(SECRET, store))
    body = json.dumps(
        {
            "type": "email.delivered",
            "created_at": "2026-08-27T12:00:00Z",
            "data": {
                "email_id": "provider-message-1",
                "to": ["must-not-be-stored@example.test"],
                "subject": "must not be stored",
            },
        },
        separators=(",", ":"),
    ).encode()
    event_id = "msg_webhook_1"
    timestamp = int(time.time())
    headers = {
        "svix-id": event_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": signature(body, event_id, timestamp),
        "content-type": "application/json",
    }
    with TestClient(app) as client:
        accepted = client.post("/v1/webhooks/resend", content=body, headers=headers)
        altered = client.post("/v1/webhooks/resend", content=body + b" ", headers=headers)
    assert accepted.status_code == 200
    assert accepted.json() == {"received": True, "duplicate": False}
    assert altered.status_code == 400
    assert len(store.calls) == 1
    event, payload_hash, _now = store.calls[0]
    assert event.provider_message_id == "provider-message-1"
    assert payload_hash == hashlib.sha256(body).digest()
    assert not hasattr(event, "recipient")


def test_resend_route_rejects_missing_or_stale_signature() -> None:
    store = RecordingStore()
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(resend_webhooks=ResendWebhookService(SECRET, store))
    body = b'{"type":"email.sent","created_at":"2026-08-27T12:00:00Z","data":{"email_id":"provider-1"}}'
    stale = int(time.time()) - 301
    with TestClient(app) as client:
        missing = client.post("/v1/webhooks/resend", content=body)
        rejected = client.post(
            "/v1/webhooks/resend",
            content=body,
            headers={
                "svix-id": "msg_stale",
                "svix-timestamp": str(stale),
                "svix-signature": signature(body, "msg_stale", stale),
            },
        )
    assert missing.status_code == 400
    assert rejected.status_code == 400
    assert not store.calls
