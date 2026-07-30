from __future__ import annotations

import sys

import pytest

from app.worker import cli


class FailingWorker:
    async def run_once(self) -> bool:
        raise RuntimeError("database-secret-marker")


def test_worker_cli_sanitizes_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TIMESTAMP_WORKER_FACTORY", "app.worker.composition:create_worker")
    monkeypatch.setattr(sys, "argv", ["timestamp-worker", "--once"])
    monkeypatch.setattr(cli, "_load_factory", lambda _path: lambda: FailingWorker())
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "event=worker_failed code=worker_runtime_failure"
