from __future__ import annotations

import re
from dataclasses import dataclass

_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestDigest:
    """The exact SHA-256 result supplied by the browser, never an input to SHA-256."""

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 32:
            raise ValueError("manifest digest must contain exactly 32 bytes")

    @classmethod
    def from_hex(cls, value: str) -> ManifestDigest:
        if not _LOWER_SHA256.fullmatch(value):
            raise ValueError("manifest digest must be 64 lowercase hexadecimal characters")
        return cls(bytes.fromhex(value))

    @classmethod
    def from_bytes(cls, value: bytes) -> ManifestDigest:
        return cls(bytes(value))

    @property
    def hex(self) -> str:
        return self.value.hex()

    def ots_target(self) -> bytes:
        """Return the 32-byte OTS target; callers must not hash these bytes again."""
        return self.value
