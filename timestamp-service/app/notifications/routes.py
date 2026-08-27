from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.notifications.webhooks import ResendWebhookService, ResendWebhookSignatureError

router = APIRouter()


@router.post("/v1/webhooks/resend")
async def resend_webhook(
    request: Request,
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> JSONResponse:
    services = getattr(request.app.state, "services", None)
    service: ResendWebhookService | None = getattr(services, "resend_webhooks", None)
    if service is None or svix_id is None or svix_timestamp is None or svix_signature is None:
        raise HTTPException(status_code=400, detail="invalid webhook")
    raw_body = await request.body()
    try:
        result = await service.process(
            raw_body,
            svix_id,
            svix_timestamp,
            svix_signature,
            datetime.now(UTC),
        )
    except (ResendWebhookSignatureError, ValueError) as error:
        raise HTTPException(status_code=400, detail="invalid webhook") from error
    return JSONResponse({"received": True, "duplicate": result.duplicate})
