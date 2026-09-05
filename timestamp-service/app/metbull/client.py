from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

METBULL_OFFICIAL_URL = "https://www.lpi.usra.edu/meteor/metbull.cfm?code={code}"
_CANONICAL_CODE = re.compile(r"^[1-9][0-9]{0,8}$")


class MetbullUnavailable(RuntimeError):
    pass


class MetbullNotFound(RuntimeError):
    pass


class MetbullNotOfficial(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetbullRecord:
    code: int
    official_url: str
    canonical_name: str
    record_status: str
    official_name: bool
    recommended_classification: str
    fall_or_find: str
    year_found: int | None
    country: str | None
    latitude: str | None
    longitude: str | None


class MetbullLookup(Protocol):
    async def lookup(self, code: int) -> MetbullRecord: ...


def validated_code(code: object) -> int:
    if type(code) is not int or _CANONICAL_CODE.fullmatch(str(code)) is None:
        raise ValueError("metbull_code_invalid")
    return code
