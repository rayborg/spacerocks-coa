from __future__ import annotations

import json
import uuid
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.jobs.models import SpecificJobRetryable
from app.tasks.dispatch import TaskDispatchCoordinator, TaskDispatchUnavailable, deterministic_task_name

_MAX_TASK_BODY = 128


class SpecificWorker(Protocol):
    async def run_specific(self, job_id: str, generation: int) -> bool: ...


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    generation: int = Field(strict=True, ge=1, le=999_999_999)


def create_task_app(
    worker: SpecificWorker,
    task_dispatch: TaskDispatchCoordinator,
    *,
    expected_queue: str,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/internal/tasks/run", status_code=204)
    async def run_task(request: Request) -> Response:
        content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        content_length = request.headers.get("content-length")
        task_header = request.headers.get("x-cloudtasks-taskname", "")
        queue_header = request.headers.get("x-cloudtasks-queuename", "")
        try:
            declared_length = int(content_length or "")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid task request") from error
        if (
            content_type != "application/json"
            or not 1 <= declared_length <= _MAX_TASK_BODY
            or queue_header != expected_queue
        ):
            raise HTTPException(status_code=400, detail="invalid task request")
        body = await request.body()
        if len(body) != declared_length or len(body) > _MAX_TASK_BODY:
            raise HTTPException(status_code=400, detail="invalid task request")
        try:
            decoded: object = json.loads(body)
            payload = TaskPayload.model_validate(decoded)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            raise HTTPException(status_code=400, detail="invalid task request") from error
        expected_name = deterministic_task_name(payload.job_id, payload.generation)
        if task_header.rsplit("/", 1)[-1] != expected_name:
            raise HTTPException(status_code=400, detail="invalid task request")
        try:
            await worker.run_specific(str(payload.job_id), payload.generation)
            await task_dispatch.reconcile(limit=100)
        except SpecificJobRetryable as error:
            raise HTTPException(status_code=503, detail="task retry required") from error
        except TaskDispatchUnavailable as error:
            raise HTTPException(status_code=503, detail="task dispatch unavailable") from error
        return Response(status_code=204)

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    return app
