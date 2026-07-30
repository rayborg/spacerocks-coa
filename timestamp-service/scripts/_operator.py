from __future__ import annotations

import importlib
import os
import re
from typing import Protocol, cast

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class OperatorCommands(Protocol):
    async def replay(self, job_id: str) -> None: ...

    async def reverify(self, order_id: str, request_id: str) -> None: ...

    async def upgrade(self, order_id: str) -> None: ...

    async def purge_synthetic(self, order_id: str, *, preserve_proofs: bool) -> None: ...


def opaque_id(value: str) -> str:
    if not _OPAQUE_ID.fullmatch(value):
        raise ValueError("identifier_must_be_opaque")
    return value


def load_commands() -> OperatorCommands:
    path = os.environ.get("TIMESTAMP_OPERATOR_FACTORY", "")
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name.startswith("app.") or not attribute.isidentifier():
        raise RuntimeError("TIMESTAMP_OPERATOR_FACTORY must name an app module factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    return cast(OperatorCommands, factory())


def require_confirmation(actual: str, expected: str) -> None:
    if actual != expected:
        raise RuntimeError("explicit_confirmation_required")
