from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from app.config.settings import Settings
from app.db.base import Base
from app.db.session import create_session_factory
from app.domain.identifiers import OrderReference
from app.notifications.worker import build_notification_worker
from app.ports.notifications import ClaimedNotification, NotificationKind, ProviderAcceptance


class RecordingOutbox:
    def __init__(self, message: ClaimedNotification) -> None:
        self.message = message
        self.accepted: ProviderAcceptance | None = None

    async def claim(self, worker_id, now, lease_for):
        del worker_id, now, lease_for
        message, self.message = self.message, None  # type: ignore[assignment]
        return message

    async def record_accepted(self, message, acceptance, now):
        del message, now
        self.accepted = acceptance

    async def record_retry(self, message, now, retry_at, safe_error_code, response_status=None):
        raise AssertionError((message, now, retry_at, safe_error_code, response_status))

    async def record_terminal_failure(self, message, now, safe_error_code, response_status=None):
        raise AssertionError((message, now, safe_error_code, response_status))


class RecordingSender:
    async def send(self, message: ClaimedNotification) -> ProviderAcceptance:
        assert message.kind in {NotificationKind.INITIAL_CONFIRMATION, NotificationKind.FINAL_CONFIRMATION}
        return ProviderAcceptance("resend-test-message", 200)


def claimed() -> ClaimedNotification:
    return ClaimedNotification(
        id="019d2e0f-7094-7000-8000-000000000001",
        message_key="bitcoin-confirmed-initial-v1-ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB",
        order_reference=OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        kind=NotificationKind.INITIAL_CONFIRMATION,
        recipient="private@example.test",
        proof_version=1,
        confirmation_count=1,
        attempt=1,
        lease_owner="notification-test",
        lease_token="019d2e0f-7094-7000-8000-000000000002",
        lease_until=datetime(2026, 8, 27, 12, 1, tzinfo=UTC),
        idempotency_key="resend-test-key",
    )


@pytest.mark.asyncio
async def test_notification_worker_is_independent_and_dispatches_supported_rows_only() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        resend_sender_mode="resend",
        resend_api_key="re_do_not_use",
        resend_sender="proofs@example.test",
    )
    worker = build_notification_worker(settings, factory, sender=RecordingSender())
    outbox = RecordingOutbox(claimed())
    worker.dispatcher.outbox = outbox  # type: ignore[assignment]
    assert await worker.run_once()
    assert outbox.accepted == ProviderAcceptance("resend-test-message", 200)
    assert not await worker.run_once()
    engine.dispose()


def test_notification_worker_fails_closed_when_sender_mode_is_disabled() -> None:
    engine = create_engine("sqlite://")
    factory = create_session_factory(engine)
    with pytest.raises(RuntimeError, match="sender_disabled"):
        build_notification_worker(Settings(_env_file=None), factory, sender=RecordingSender())
    engine.dispose()
