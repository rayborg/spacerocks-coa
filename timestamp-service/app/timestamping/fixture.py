from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation

from app.domain.digest import ManifestDigest
from app.ports.timestamping import PendingProof
from app.timestamping.detached import (
    deserialize_exact_proof,
    new_detached_exact_digest,
    serialize_detached,
)


def require_test_process() -> None:
    if os.environ.get("APP_ENV", "").lower() != "test" or "pytest" not in sys.modules:
        raise RuntimeError("fixture_adapter_forbidden_outside_test")


class DisabledTimestamper:
    async def stamp_exact_digest(self, digest: ManifestDigest) -> PendingProof:
        del digest
        raise RuntimeError("timestamping_disabled")

    async def upgrade_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> bytes:
        del digest, proof_bytes
        raise RuntimeError("timestamping_disabled")


class FixtureTimestamper:
    def __init__(self, *, now: datetime | None = None, confirm_on_upgrade: bool = False) -> None:
        require_test_process()
        self._now = now or datetime(2026, 7, 30, 12, 5, tzinfo=UTC)
        self._confirm_on_upgrade = confirm_on_upgrade
        self.stamp_calls = 0
        self.upgrade_calls = 0

    async def stamp_exact_digest(self, digest: ManifestDigest) -> PendingProof:
        self.stamp_calls += 1
        detached = new_detached_exact_digest(digest)
        detached.timestamp.attestations.update(
            {
                PendingAttestation("https://fixture-a.invalid/"),
                PendingAttestation("https://fixture-b.invalid/"),
            }
        )
        proof_bytes = serialize_detached(detached)
        return PendingProof(proof_bytes=proof_bytes, calendar_submitted_at=self._now)

    async def upgrade_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> bytes:
        self.upgrade_calls += 1
        parsed = deserialize_exact_proof(digest, proof_bytes)
        if self._confirm_on_upgrade:
            parsed.detached.timestamp.attestations.add(BitcoinBlockHeaderAttestation(900_000))
        upgraded = serialize_detached(parsed.detached)
        deserialize_exact_proof(digest, upgraded)
        return upgraded
