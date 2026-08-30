from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.digest import ManifestDigest


@dataclass(frozen=True, slots=True)
class BitcoinVerification:
    verified: bool
    method: str
    verified_at: datetime | None = None
    block_height: int | None = None
    block_hash: str | None = None
    block_time: datetime | None = None
    confirmation_policy: str | None = None
    confirmations: int | None = None

    def __post_init__(self) -> None:
        metadata = (self.verified_at, self.block_height, self.block_hash, self.block_time, self.confirmation_policy)
        if self.verified and any(value is None for value in metadata):
            raise ValueError("verified Bitcoin result requires complete metadata")
        if not self.verified and any(value is not None for value in metadata):
            raise ValueError("unverified Bitcoin result cannot carry confirmation metadata")
        if self.verified and (
            self.confirmations is None or isinstance(self.confirmations, bool) or self.confirmations < 1
        ):
            raise ValueError("verified Bitcoin result requires at least one confirmation")
        if not self.verified and self.confirmations is not None and (
            isinstance(self.confirmations, bool) or self.confirmations < 0
        ):
            raise ValueError("Bitcoin confirmation count is invalid")


class BitcoinVerifierUnavailable(RuntimeError):
    """The trusted verifier cannot currently provide a reliable answer."""


class BitcoinEvidenceInvalid(ValueError):
    """The proof or node response contradicts required Bitcoin evidence invariants."""


class BitcoinVerifier(Protocol):
    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification: ...


class DisabledBitcoinVerifier:
    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
        del digest, proof_bytes
        return BitcoinVerification(verified=False, method="disabled")
