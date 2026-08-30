from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.domain.identifiers import OrderReference
from app.ports.bitcoin import BitcoinVerification


class NotificationKind(StrEnum):
    INITIAL_CONFIRMATION = "bitcoin-confirmed-initial"
    FINAL_CONFIRMATION = "bitcoin-confirmed-final"


_MINIMUM_CONFIRMATIONS = {
    NotificationKind.INITIAL_CONFIRMATION: 1,
    NotificationKind.FINAL_CONFIRMATION: 6,
}


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_key: str = field(repr=False)
    order_reference: OrderReference
    template: str = field(repr=False)
    recipient: str = field(repr=False)
    variables: Mapping[str, str] = field(repr=False)
    proof_version: int | None = None
    confirmation_count: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimedNotification:
    id: str
    message_key: str = field(repr=False)
    order_reference: OrderReference
    kind: NotificationKind = field(repr=False)
    recipient: str = field(repr=False)
    proof_version: int = 0
    confirmation_count: int = 0
    attempt: int = 0
    lease_owner: str = ""
    lease_token: str = field(default="", repr=False)
    lease_until: datetime | None = None
    idempotency_key: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class ProviderAcceptance:
    message_id: str
    response_status: int


@dataclass(frozen=True, slots=True)
class VerifiedResendEvent:
    svix_event_id: str
    event_type: str
    provider_message_id: str
    event_created_at: datetime


@dataclass(frozen=True, slots=True)
class WebhookProcessResult:
    duplicate: bool
    notification_delivered: bool = False
    order_transitioned: bool = False


@dataclass(frozen=True, slots=True)
class ConfirmationObservation:
    id: str
    order_id: str
    proof_version: int
    confirmations: int
    block_height: int
    block_hash: str
    method: str
    confirmation_policy: str
    observed_at: datetime
    event_key: str


def notification_message(
    kind: NotificationKind,
    order_reference: OrderReference,
    recipient: str,
    proof_version: int,
    confirmation_count: int,
) -> OutboxMessage:
    if (
        isinstance(proof_version, bool)
        or isinstance(confirmation_count, bool)
        or not 1 <= proof_version <= 2_147_483_647
        or not _MINIMUM_CONFIRMATIONS[kind] <= confirmation_count <= 2_147_483_647
    ):
        raise ValueError("notification_milestone_invalid")
    return OutboxMessage(
        message_key=f"{kind.value}-v{proof_version}-{order_reference.value}",
        order_reference=order_reference,
        template=kind.value,
        recipient=recipient,
        variables={"order_reference": order_reference.value},
        proof_version=proof_version,
        confirmation_count=confirmation_count,
    )


class EmailSender(Protocol):
    async def send(self, message: ClaimedNotification) -> ProviderAcceptance: ...


class Outbox(Protocol):
    async def enqueue(self, message: OutboxMessage) -> None: ...


class NotificationOutbox(Outbox, Protocol):
    async def claim(
        self,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ClaimedNotification | None: ...

    async def record_accepted(
        self,
        message: ClaimedNotification,
        acceptance: ProviderAcceptance,
        now: datetime,
    ) -> None: ...


    async def record_retry(
        self,
        message: ClaimedNotification,
        now: datetime,
        retry_at: datetime,
        safe_error_code: str,
        response_status: int | None = None,
    ) -> None: ...

    async def record_terminal_failure(
        self,
        message: ClaimedNotification,
        now: datetime,
        safe_error_code: str,
        response_status: int | None = None,
    ) -> None: ...


class ConfirmationObservationRepository(Protocol):
    async def record_once(
        self,
        order_id: str,
        proof_version: int,
        event_key: str,
        verification: BitcoinVerification,
    ) -> ConfirmationObservation: ...

    async def latest(self, order_id: str, proof_version: int) -> ConfirmationObservation | None: ...
