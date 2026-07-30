from __future__ import annotations

from dataclasses import dataclass

from app.domain.order import FulfillmentState
from app.fulfillment.ports import FulfillmentRepository


@dataclass(slots=True)
class TerminalFailureHandler:
    orders: FulfillmentRepository

    async def __call__(self, order_id: str, safe_code: str) -> None:
        await self.orders.transition_fulfillment_once(
            order_id,
            FulfillmentState.MANUAL_REVIEW,
            f"worker-terminal-{safe_code}",
        )
