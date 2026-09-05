from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.jobs.models import SpecificJobRetryable
from app.tasks.dispatch import ReconcileResult, deterministic_task_name
from app.tasks.http import create_task_app


class Worker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.retry = False

    async def run_specific(self, job_id: str, generation: int) -> bool:
        if self.retry:
            raise SpecificJobRetryable("busy")
        self.calls.append((job_id, generation))
        return True


class Coordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self, *, limit: int = 100, order_id: uuid.UUID | None = None) -> ReconcileResult:
        assert limit == 100 and order_id is None
        self.calls += 1
        return ReconcileResult(0, 0)


def request(job_id: uuid.UUID, generation: int = 1) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {"generation": generation, "job_id": str(job_id)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return body, {
        "Content-Type": "application/json",
        "X-CloudTasks-QueueName": "timestamp-jobs",
        "X-CloudTasks-TaskName": deterministic_task_name(job_id, generation),
    }


def test_private_task_endpoint_accepts_only_exact_non_pii_envelope() -> None:
    worker = Worker()
    coordinator = Coordinator()
    app = create_task_app(worker, coordinator, expected_queue="timestamp-jobs")  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    body, headers = request(job_id)
    with TestClient(app) as client:
        response = client.post("/internal/tasks/run", content=body, headers=headers)
        assert response.status_code == 204 and response.content == b""
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    assert worker.calls == [(str(job_id), 1)]
    assert coordinator.calls == 1
    assert b"@" not in body and b"token" not in body.lower()


@pytest.mark.parametrize("mutation", ["extra", "queue", "name", "type", "oversized"])
def test_task_endpoint_rejects_extra_fields_wrong_headers_types_and_oversize(mutation: str) -> None:
    worker = Worker()
    coordinator = Coordinator()
    app = create_task_app(worker, coordinator, expected_queue="timestamp-jobs")  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    body, headers = request(job_id)
    if mutation == "extra":
        body = json.dumps({"job_id": str(job_id), "generation": 1, "email": "private@example.test"}).encode()
    elif mutation == "queue":
        headers["X-CloudTasks-QueueName"] = "wrong"
    elif mutation == "name":
        headers["X-CloudTasks-TaskName"] = deterministic_task_name(uuid.uuid4(), 1)
    elif mutation == "type":
        body = json.dumps({"job_id": str(job_id), "generation": True}).encode()
    else:
        body = b"{" + b" " * 200 + b"}"
    with TestClient(app) as client:
        response = client.post("/internal/tasks/run", content=body, headers=headers)
    assert response.status_code == 400
    assert worker.calls == [] and coordinator.calls == 0


def test_task_endpoint_returns_retryable_status_for_active_or_missing_job() -> None:
    worker = Worker()
    worker.retry = True
    coordinator = Coordinator()
    app = create_task_app(worker, coordinator, expected_queue="timestamp-jobs")  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    body, headers = request(job_id)
    with TestClient(app) as client:
        response = client.post("/internal/tasks/run", content=body, headers=headers)
    assert response.status_code == 503
    assert coordinator.calls == 0
