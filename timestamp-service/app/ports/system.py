from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class RandomSource(Protocol):
    def bytes(self, length: int) -> bytes: ...

    def uniform(self, lower: float, upper: float) -> float: ...
