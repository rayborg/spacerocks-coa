from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.order import FulfillmentState, OrderState
from app.ports.bitcoin import BitcoinVerification


@dataclass(frozen=True, slots=True)
class FulfillmentOrder:
    id: str
    state: OrderState
    calendar_submitted_at: datetime | None = None


class FulfillmentRepository(Protocol):
    async def get_for_fulfillment(self, order_id: str) -> FulfillmentOrder | None: ...

    async def transition_fulfillment_once(
        self,
        order_id: str,
        target: FulfillmentState,
        event_key: str,
        *,
        calendar_submitted_at: datetime | None = None,
    ) -> FulfillmentOrder: ...


class BundleRepository(Protocol):
    async def put_once(self, order_id: str, proof_version: int, bundle: bytes) -> None: ...


class VerificationRepository(Protocol):
    async def put_verified_once(self, order_id: str, proof_version: int, result: BitcoinVerification) -> None: ...

    async def get_verified(self, order_id: str, proof_version: int) -> BitcoinVerification | None: ...
