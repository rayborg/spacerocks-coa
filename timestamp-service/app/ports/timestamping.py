from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.digest import ManifestDigest


@dataclass(frozen=True, slots=True)
class PendingProof:
    proof_bytes: bytes
    calendar_submitted_at: datetime


class Timestamper(Protocol):
    async def stamp_exact_digest(self, digest: ManifestDigest) -> PendingProof:
        """Stamp digest.ots_target() directly; do not hash hex text or digest bytes."""
        ...

    async def upgrade_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> bytes:
        """Upgrade proof bytes while preserving the exact original 32-byte target."""
        ...
