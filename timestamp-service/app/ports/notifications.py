from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.domain.identifiers import OrderReference


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_key: str
    order_reference: OrderReference
    template: str
    recipient: str
    variables: Mapping[str, str]


class EmailSender(Protocol):
    async def send(self, message: OutboxMessage) -> str: ...


class Outbox(Protocol):
    async def enqueue(self, message: OutboxMessage) -> None: ...
