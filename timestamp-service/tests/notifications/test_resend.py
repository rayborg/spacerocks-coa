from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.domain.identifiers import OrderReference
from app.notifications.resend import ResendEmailSender, ResendPermanentError, ResendRetryableError
from app.notifications.templates import render_notification
from app.ports.notifications import ClaimedNotification, NotificationKind, notification_message

ORDER_REFERENCE = OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB")


def claimed(kind: NotificationKind = NotificationKind.INITIAL_CONFIRMATION) -> ClaimedNotification:
    return ClaimedNotification(
        id="019d2e0f-7094-7000-8000-000000000001",
        message_key=f"{kind.value}-v1-{ORDER_REFERENCE.value}",
        order_reference=ORDER_REFERENCE,
        kind=kind,
        recipient="private@example.test",
        proof_version=1,
        confirmation_count=1 if kind == NotificationKind.INITIAL_CONFIRMATION else 6,
        attempt=1,
        lease_owner="worker-a",
        lease_token="019d2e0f-7094-7000-8000-000000000002",
        lease_until=datetime(2026, 8, 27, 12, 1, tzinfo=UTC),
        idempotency_key="resend-deterministic-key",
    )


@pytest.mark.asyncio
async def test_resend_sender_uses_exact_endpoint_idempotency_and_minimal_template() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["idempotency"] = request.headers["Idempotency-Key"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "resend-message-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender("re_secret", "Timestamp Service <proofs@example.test>", client=client)
        result = await sender.send(claimed())

    assert result.message_id == "resend-message-1"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["authorization"] == "Bearer re_secret"
    assert captured["idempotency"] == "resend-deterministic-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["to"] == ["private@example.test"]
    serialized = json.dumps(payload)
    for forbidden in ("token", "digest", "certificate", "proof_bytes"):
        assert forbidden not in serialized.lower()
    assert "private@example.test" not in repr(claimed())


@pytest.mark.asyncio
async def test_resend_sender_classifies_rejection_and_ambiguous_acceptance() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(422, json={"message": "bad"}))
    ) as client:
        with pytest.raises(ResendPermanentError) as permanent:
            await ResendEmailSender("re_secret", "proofs@example.test", client=client).send(claimed())
    assert permanent.value.response_status == 422

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not-json"))
    ) as client:
        with pytest.raises(ResendRetryableError, match="ambiguous"):
            await ResendEmailSender("re_secret", "proofs@example.test", client=client).send(claimed())

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ResendRetryableError, match="ambiguous"):
            await ResendEmailSender("re_secret", "proofs@example.test", client=client).send(claimed())


def test_initial_and_final_templates_are_distinct_and_data_minimized() -> None:
    initial = claimed()
    final = claimed(NotificationKind.FINAL_CONFIRMATION)
    assert initial.message_key != final.message_key
    assert initial.confirmation_count == 1
    assert final.confirmation_count == 6
    initial_template = render_notification(initial.kind, initial.order_reference)
    final_template = render_notification(final.kind, final.order_reference)
    assert initial_template.subject != final_template.subject
    assert "first Bitcoin confirmation" in initial_template.text
    assert "six Bitcoin confirmations" in final_template.text
    queued = notification_message(
        NotificationKind.INITIAL_CONFIRMATION,
        ORDER_REFERENCE,
        "private@example.test",
        1,
        1,
    )
    assert "private@example.test" not in repr(queued)
    assert NotificationKind.INITIAL_CONFIRMATION.value not in repr(queued)
    with pytest.raises(ValueError, match="milestone"):
        notification_message(
            NotificationKind.FINAL_CONFIRMATION,
            ORDER_REFERENCE,
            "private@example.test",
            1,
            5,
        )
