from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Protocol

from app.ports.notifications import VerifiedResendEvent, WebhookProcessResult

_SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9_=-]{1,128}$")
_SAFE_EVENT_TYPE = re.compile(r"^email\.[a-z_]{1,64}$")
_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_WEBHOOK_BYTES = 256 * 1024


class ResendWebhookSignatureError(ValueError):
    pass


class ResendWebhookStore(Protocol):
    async def process(
        self,
        event: VerifiedResendEvent,
        payload_sha256: bytes,
        now: datetime,
    ) -> WebhookProcessResult: ...


class ResendWebhookService:
    def __init__(
        self,
        signing_secret: str,
        store: ResendWebhookStore,
        *,
        tolerance_seconds: int = 300,
    ) -> None:
        if not signing_secret.startswith("whsec_"):
            raise ValueError("resend_webhook_secret_invalid")
        try:
            secret = base64.b64decode(signing_secret.removeprefix("whsec_"), validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("resend_webhook_secret_invalid") from error
        if len(secret) < 16 or not 1 <= tolerance_seconds <= 900:
            raise ValueError("resend_webhook_configuration_invalid")
        self._secret = secret
        self._store = store
        self._tolerance_seconds = tolerance_seconds

    async def process(
        self,
        raw_body: bytes,
        svix_id: str,
        svix_timestamp: str,
        svix_signature: str,
        now: datetime,
    ) -> WebhookProcessResult:
        if not raw_body or len(raw_body) > _MAX_WEBHOOK_BYTES or not _SAFE_EVENT_ID.fullmatch(svix_id):
            raise ResendWebhookSignatureError("invalid_resend_webhook")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resend_webhook_time_invalid")
        try:
            signed_at = int(svix_timestamp)
        except ValueError as error:
            raise ResendWebhookSignatureError("invalid_resend_webhook") from error
        if abs(int(now.timestamp()) - signed_at) > self._tolerance_seconds:
            raise ResendWebhookSignatureError("invalid_resend_webhook")
        signed = f"{svix_id}.{svix_timestamp}.".encode("ascii") + raw_body
        expected = base64.b64encode(hmac.new(self._secret, signed, hashlib.sha256).digest()).decode("ascii")
        supplied = _v1_signatures(svix_signature)
        if not supplied or not any(hmac.compare_digest(expected, value) for value in supplied):
            raise ResendWebhookSignatureError("invalid_resend_webhook")
        event = _parse_event(raw_body, svix_id)
        return await self._store.process(event, hashlib.sha256(raw_body).digest(), now)


def _v1_signatures(header: str) -> tuple[str, ...]:
    values: list[str] = []
    for item in header.split():
        try:
            version, signature = item.split(",", 1)
        except ValueError:
            continue
        if version == "v1" and signature:
            values.append(signature)
    return tuple(values)


def _parse_event(raw_body: bytes, svix_id: str) -> VerifiedResendEvent:
    try:
        payload = json.loads(raw_body)
        event_type = payload["type"]
        created_at = payload["created_at"]
        provider_message_id = payload["data"]["email_id"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ResendWebhookSignatureError("invalid_resend_webhook_payload") from error
    if (
        not isinstance(event_type, str)
        or not _SAFE_EVENT_TYPE.fullmatch(event_type)
        or not isinstance(provider_message_id, str)
        or not _SAFE_PROVIDER_ID.fullmatch(provider_message_id)
        or not isinstance(created_at, str)
    ):
        raise ResendWebhookSignatureError("invalid_resend_webhook_payload")
    try:
        parsed_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ResendWebhookSignatureError("invalid_resend_webhook_payload") from error
    if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
        raise ResendWebhookSignatureError("invalid_resend_webhook_payload")
    return VerifiedResendEvent(
        svix_event_id=svix_id,
        event_type=event_type,
        provider_message_id=provider_message_id,
        event_created_at=parsed_time.astimezone(UTC),
    )
