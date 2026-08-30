from __future__ import annotations

import re

import httpx

from app.notifications.templates import render_notification
from app.ports.notifications import ClaimedNotification, ProviderAcceptance

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ResendRetryableError(RuntimeError):
    def __init__(self, safe_error_code: str, response_status: int | None = None) -> None:
        super().__init__(safe_error_code)
        self.safe_error_code = safe_error_code
        self.response_status = response_status


class ResendPermanentError(RuntimeError):
    def __init__(self, safe_error_code: str, response_status: int | None = None) -> None:
        super().__init__(safe_error_code)
        self.safe_error_code = safe_error_code
        self.response_status = response_status


class ResendEmailSender:
    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.startswith("re_") or len(api_key) > 512 or "\r" in api_key or "\n" in api_key:
            raise ValueError("resend_api_key_invalid")
        if not 3 <= len(sender) <= 254 or "@" not in sender or "\r" in sender or "\n" in sender:
            raise ValueError("resend_sender_invalid")
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ValueError("resend_timeout_invalid")
        self._api_key = api_key
        self._sender = sender
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client

    async def send(self, message: ClaimedNotification) -> ProviderAcceptance:
        rendered = render_notification(message.kind, message.order_reference)
        request = {
            "from": self._sender,
            "to": [message.recipient],
            "subject": rendered.subject,
            "text": rendered.text,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Idempotency-Key": message.idempotency_key,
            "Accept": "application/json",
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=self._timeout,
                    trust_env=False,
                ) as client:
                    response = await client.post(_RESEND_ENDPOINT, headers=headers, json=request)
            else:
                response = await self._client.post(
                    _RESEND_ENDPOINT,
                    headers=headers,
                    json=request,
                    timeout=self._timeout,
                    follow_redirects=False,
                )
        except httpx.TransportError as error:
            raise ResendRetryableError("resend_request_ambiguous") from error

        if response.status_code != 200:
            error_type = (
                ResendRetryableError
                if response.status_code in {408, 425, 429} or response.status_code >= 500
                else ResendPermanentError
            )
            raise error_type("resend_response_rejected", response.status_code)
        content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != "application/json" or len(response.content) > 64 * 1024:
            raise ResendRetryableError("resend_acceptance_ambiguous", response.status_code)
        try:
            payload = response.json()
        except ValueError as error:
            raise ResendRetryableError("resend_acceptance_ambiguous", response.status_code) from error
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not _SAFE_PROVIDER_ID.fullmatch(message_id):
            raise ResendRetryableError("resend_acceptance_ambiguous", response.status_code)
        return ProviderAcceptance(message_id=message_id, response_status=response.status_code)
