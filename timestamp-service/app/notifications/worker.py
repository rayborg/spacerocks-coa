from __future__ import annotations

import asyncio
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import AppEnvironment, ResendSenderMode, Settings
from app.db.notification_adapters import SqlNotificationOutbox
from app.db.session import create_database_engine, create_session_factory
from app.notifications.dispatch import NotificationDispatcher
from app.notifications.resend import ResendEmailSender
from app.ports.notifications import EmailSender

_OPAQUE_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(slots=True)
class NotificationWorker:
    worker_id: str
    dispatcher: NotificationDispatcher

    def __post_init__(self) -> None:
        if not _OPAQUE_WORKER_ID.fullmatch(self.worker_id):
            raise ValueError("notification_worker_id_must_be_opaque")

    async def run_once(self) -> bool:
        return await self.dispatcher.dispatch_once(self.worker_id, datetime.now(UTC))

    async def run_forever(self, *, idle_seconds: float = 1.0) -> None:
        if not 0.05 <= idle_seconds <= 60:
            raise ValueError("notification_worker_idle_interval_invalid")
        while True:
            handled = await self.run_once()
            if not handled:
                await asyncio.sleep(idle_seconds)


def build_notification_worker(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    worker_id: str = "notification-worker",
    sender: EmailSender | None = None,
) -> NotificationWorker:
    if settings.resend_sender_mode != ResendSenderMode.RESEND:
        raise RuntimeError("notification_sender_disabled")
    if sender is not None and settings.app_env != AppEnvironment.TEST:
        raise RuntimeError("notification_sender_override_forbidden")
    if sender is None:
        if settings.resend_api_key is None or settings.resend_sender is None:
            raise RuntimeError("validated_resend_sender_configuration_unavailable")
        sender = ResendEmailSender(
            settings.resend_api_key.get_secret_value(),
            settings.resend_sender,
            timeout_seconds=settings.resend_api_timeout_seconds,
        )
    return NotificationWorker(
        worker_id=worker_id,
        dispatcher=NotificationDispatcher(SqlNotificationOutbox(session_factory), sender),
    )


def create_notification_worker() -> NotificationWorker:
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("notification_worker_database_required")
    engine = create_database_engine(settings.database_url.get_secret_value())
    factory = create_session_factory(engine)
    worker_id = os.environ.get("NOTIFICATION_WORKER_ID") or f"notification-{secrets.token_hex(8)}"
    return build_notification_worker(settings, factory, worker_id=worker_id)
