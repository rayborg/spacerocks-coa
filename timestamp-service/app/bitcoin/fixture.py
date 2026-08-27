from __future__ import annotations

from datetime import UTC, datetime

from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinVerification
from app.timestamping.detached import deserialize_exact_proof
from app.timestamping.fixture import require_test_process


class FixtureBitcoinVerifier:
    def __init__(self, *, block_hash: str = "c" * 64) -> None:
        require_test_process()
        self._block_hash = block_hash

    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
        parsed = deserialize_exact_proof(digest, proof_bytes)
        attestations = [
            attestation
            for _message, attestation in parsed.detached.timestamp.all_attestations()
            if type(attestation) is BitcoinBlockHeaderAttestation
        ]
        if not attestations:
            return BitcoinVerification(verified=False, method="fixture-no-bitcoin-attestation")
        height = min(attestation.height for attestation in attestations)
        return BitcoinVerification(
            verified=True,
            method="fixture-exact-digest",
            verified_at=datetime(2026, 7, 30, 15, 10, tzinfo=UTC),
            block_height=height,
            block_hash=self._block_hash,
            block_time=datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
            confirmation_policy="phase0-fixture-exact-target",
            confirmations=6,
        )
