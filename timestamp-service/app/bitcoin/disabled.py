from __future__ import annotations

from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinVerification
from app.timestamping.detached import validate_exact_proof


class DisabledVerifier:
    """Production-safe default: structural validity can never become confirmation."""

    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
        validate_exact_proof(digest, proof_bytes)
        return BitcoinVerification(verified=False, method="disabled")
