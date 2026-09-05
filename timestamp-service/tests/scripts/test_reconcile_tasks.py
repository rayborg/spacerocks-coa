from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from app.tasks.dispatch import MIN_STALE_DISPATCH_GRACE
from scripts import reconcile_tasks


class Commands:
    def __init__(self) -> None:
        self.limit = 0
        self.stale_grace: timedelta | None = None

    async def reconcile_tasks(self, limit: int) -> tuple[int, int]:
        self.limit = limit
        return 7, 5

    async def recover_stale_tasks(self, limit: int, stale_grace: timedelta) -> tuple[int, int]:
        self.limit = limit
        self.stale_grace = stale_grace
        return 3, 2


def test_reconcile_command_is_bounded_and_reports_safe_counts(monkeypatch, capsys) -> None:
    commands = Commands()
    monkeypatch.setattr(reconcile_tasks, "load_commands", lambda: commands)
    monkeypatch.setattr(sys, "argv", ["reconcile_tasks", "--limit", "25"])
    assert reconcile_tasks.main() == 0
    assert commands.limit == 25
    assert commands.stale_grace is None
    assert capsys.readouterr().out.strip() == "event=tasks_reconciled selected=7 dispatched=5"


def test_reconcile_command_requires_explicit_recovery_mode(monkeypatch, capsys) -> None:
    commands = Commands()
    grace_seconds = int(MIN_STALE_DISPATCH_GRACE.total_seconds())
    monkeypatch.setattr(reconcile_tasks, "load_commands", lambda: commands)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconcile_tasks",
            "--limit",
            "10",
            "--recover-stale-dispatched",
            "--stale-grace-seconds",
            str(grace_seconds),
        ],
    )
    assert reconcile_tasks.main() == 0
    assert commands.limit == 10
    assert commands.stale_grace == MIN_STALE_DISPATCH_GRACE
    assert capsys.readouterr().out.strip() == (
        f"event=stale_tasks_recovered selected=3 dispatched=2 stale_grace_seconds={grace_seconds}"
    )


def test_reconcile_command_rejects_unsafe_stale_grace(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["reconcile_tasks", "--stale-grace-seconds", "1"])
    with pytest.raises(SystemExit, match="2"):
        reconcile_tasks.main()
