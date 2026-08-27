from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.notifications.resend import ResendPermanentError, ResendRetryableError
from app.ports.notifications import EmailSender, NotificationOutbox


@dataclass(slots=True)
class NotificationDispatcher:
    outbox: NotificationOutbox
    sender: EmailSender
    lease_for: timedelta = timedelta(seconds=30)

    async def dispatch_once(self, worker_id: str, now: datetime) -> bool:
        message = await self.outbox.claim(worker_id, now, self.lease_for)
        if message is None:
            return False
        try:
            acceptance = await self.sender.send(message)
        except ResendRetryableError as error:
            delay_seconds = min(21600, 30 * (2 ** (message.attempt - 1)))
            await self.outbox.record_retry(
                message,
                now,
                now + timedelta(seconds=delay_seconds),
                error.safe_error_code,
                error.response_status,
            )
        except ResendPermanentError as error:
            await self.outbox.record_terminal_failure(
                message,
                now,
                error.safe_error_code,
                error.response_status,
            )
        else:
            await self.outbox.record_accepted(message, acceptance, now)
        return True
