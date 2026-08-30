from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.identifiers import OrderReference
from app.ports.notifications import NotificationKind


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str = field(repr=False)
    text: str = field(repr=False)


def render_notification(kind: NotificationKind, order_reference: OrderReference) -> RenderedEmail:
    if kind == NotificationKind.INITIAL_CONFIRMATION:
        return RenderedEmail(
            subject="Your timestamp has its first Bitcoin confirmation",
            text=(
                "Your timestamp proof has reached its first Bitcoin confirmation.\n\n"
                f"Order reference: {order_reference.value}\n\n"
                "Keep your proof bundle in a safe place."
            ),
        )
    if kind == NotificationKind.FINAL_CONFIRMATION:
        return RenderedEmail(
            subject="Your timestamp has six Bitcoin confirmations",
            text=(
                "Your timestamp proof has reached six Bitcoin confirmations.\n\n"
                f"Order reference: {order_reference.value}\n\n"
                "This is the final confirmation notice."
            ),
        )
    raise ValueError("notification_template_invalid")
