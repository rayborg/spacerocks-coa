from __future__ import annotations

import sys

import pytest

from scripts import reverify_order


class FakeCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reverify(self, order_id: str, request_id: str) -> None:
        self.calls.append((order_id, request_id))


def _arguments(order_id: str, request_id: str, confirmation: str) -> list[str]:
    return [
        "reverify-order",
        order_id,
        "--request-id",
        request_id,
        "--confirm",
        confirmation,
    ]


def test_missing_and_invalid_request_ids_fail_argument_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["reverify-order", "order_opaque", "--confirm", "unused"])
    with pytest.raises(SystemExit) as missing:
        reverify_order.main()
    assert missing.value.code == 2

    monkeypatch.setattr(
        sys,
        "argv",
        _arguments("order_opaque", "invalid/request", "REVERIFY:order_opaque:invalid/request"),
    )
    with pytest.raises(SystemExit) as invalid:
        reverify_order.main()
    assert invalid.value.code == 2

    overlong = "r" * 33
    monkeypatch.setattr(
        sys,
        "argv",
        _arguments("order_opaque", overlong, f"REVERIFY:order_opaque:{overlong}"),
    )
    with pytest.raises(SystemExit) as too_long:
        reverify_order.main()
    assert too_long.value.code == 2


def test_confirmation_binds_order_and_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = FakeCommands()
    monkeypatch.setattr(reverify_order, "load_commands", lambda: commands)
    monkeypatch.setattr(
        sys,
        "argv",
        _arguments("order_opaque", "change_001", "REVERIFY:order_opaque:different"),
    )
    with pytest.raises(RuntimeError, match="explicit_confirmation_required"):
        reverify_order.main()
    assert commands.calls == []


def test_duplicate_and_distinct_request_ids_remain_explicit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands = FakeCommands()
    monkeypatch.setattr(reverify_order, "load_commands", lambda: commands)
    for request_id in ("change_001", "change_001", "change_002"):
        monkeypatch.setattr(
            sys,
            "argv",
            _arguments("order_opaque", request_id, f"REVERIFY:order_opaque:{request_id}"),
        )
        assert reverify_order.main() == 0

    assert commands.calls == [
        ("order_opaque", "change_001"),
        ("order_opaque", "change_001"),
        ("order_opaque", "change_002"),
    ]
    output = capsys.readouterr().out
    assert "request_id=change_001" in output
    assert "request_id=change_002" in output
