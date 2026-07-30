from __future__ import annotations

import re
from dataclasses import dataclass

_CERTIFICATE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ORDER_REFERENCE = re.compile(r"^ts_[0-9A-HJKMNP-TV-Z]{26}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CertificateReference:
    value: str

    def __post_init__(self) -> None:
        if not _CERTIFICATE.fullmatch(self.value):
            raise ValueError("invalid certificate reference")


@dataclass(frozen=True, slots=True)
class OrderReference:
    value: str

    def __post_init__(self) -> None:
        if not _ORDER_REFERENCE.fullmatch(self.value):
            raise ValueError("invalid order reference")


@dataclass(frozen=True, slots=True)
class ProviderReference:
    value: str

    def __post_init__(self) -> None:
        if not _OPAQUE_ID.fullmatch(self.value):
            raise ValueError("invalid provider reference")
