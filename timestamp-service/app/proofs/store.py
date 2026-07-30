from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime

from app.domain.digest import ManifestDigest
from app.domain.identifiers import OrderReference
from app.ports.bitcoin import BitcoinVerification
from app.ports.proof import ProofState, StoredProof
from app.timestamping.detached import validate_exact_proof


def make_stored_proof(
    order_reference: OrderReference,
    version: int,
    target_digest: ManifestDigest,
    proof_bytes: bytes,
    *,
    proof_state: ProofState,
    calendar_submitted_at: datetime,
    verification: BitcoinVerification | None,
) -> StoredProof:
    if version < 1:
        raise ValueError("proof_version_must_be_positive")
    proof_bytes = bytes(proof_bytes)
    validate_exact_proof(target_digest, proof_bytes)
    return StoredProof(
        order_reference=order_reference,
        version=version,
        target_digest=target_digest,
        proof_bytes=proof_bytes,
        proof_sha256=hashlib.sha256(proof_bytes).digest(),
        proof_state=proof_state,
        calendar_submitted_at=calendar_submitted_at,
        verification=verification,
    )


class InMemoryProofStore:
    """Strict append-only port implementation for deterministic tests and local wiring."""

    def __init__(self) -> None:
        self._proofs: dict[OrderReference, tuple[StoredProof, ...]] = {}
        self._lock = asyncio.Lock()

    async def append(self, proof: StoredProof) -> None:
        validate_exact_proof(proof.target_digest, proof.proof_bytes, expected_sha256=proof.proof_sha256)
        if proof.version < 1:
            raise ValueError("proof_version_must_be_positive")
        async with self._lock:
            existing = self._proofs.get(proof.order_reference, ())
            if existing:
                latest = existing[-1]
                if proof.target_digest != latest.target_digest:
                    raise ValueError("proof_target_is_immutable")
                if proof.version != latest.version + 1:
                    if proof.version == latest.version and proof == latest:
                        return
                    raise ValueError("proof_version_must_append")
            elif proof.version != 1:
                raise ValueError("first_proof_version_must_be_one")
            self._proofs[proof.order_reference] = (*existing, proof)

    async def latest(self, order_reference: OrderReference) -> StoredProof | None:
        async with self._lock:
            existing = self._proofs.get(order_reference, ())
            return existing[-1] if existing else None

    async def versions(self, order_reference: OrderReference) -> tuple[StoredProof, ...]:
        async with self._lock:
            return self._proofs.get(order_reference, ())
