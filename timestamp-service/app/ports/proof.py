from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.ports.bitcoin import BitcoinVerification

MAX_PROOF_BYTES = 262_144


class ProofState(StrEnum):
    CALENDAR_PENDING = "calendar_pending"
    BITCOIN_VERIFIED = "bitcoin_verified"


@dataclass(frozen=True, slots=True)
class StoredProof:
    order_reference: OrderReference
    version: int
    target_digest: ManifestDigest
    proof_bytes: bytes
    proof_sha256: bytes
    proof_state: ProofState
    calendar_submitted_at: datetime
    verification: BitcoinVerification | None

    def __post_init__(self) -> None:
        proof_bytes = bytes(self.proof_bytes)
        proof_sha256 = bytes(self.proof_sha256)
        object.__setattr__(self, "proof_bytes", proof_bytes)
        object.__setattr__(self, "proof_sha256", proof_sha256)
        if self.version < 1:
            raise ValueError("proof_version_must_be_positive")
        if not 1 <= len(proof_bytes) <= MAX_PROOF_BYTES:
            raise ValueError("proof_size_invalid")
        if hashlib.sha256(proof_bytes).digest() != proof_sha256:
            raise ValueError("proof_checksum_mismatch")
        if self.calendar_submitted_at.tzinfo is None or self.calendar_submitted_at.utcoffset() is None:
            raise ValueError("calendar_submission_time_must_be_timezone_aware")
        if self.proof_state == ProofState.CALENDAR_PENDING:
            if self.verification is not None:
                raise ValueError("pending_proof_cannot_carry_verification")
        elif self.proof_state == ProofState.BITCOIN_VERIFIED:
            if self.verification is None or not self.verification.verified:
                raise ValueError("verified_proof_requires_complete_verification")
            _validate_verification(self.verification)
        else:
            raise ValueError("invalid_proof_state")

    @property
    def proof_byte_length(self) -> int:
        return len(self.proof_bytes)


class ProofStore(Protocol):
    async def append(self, proof: StoredProof) -> None: ...

    async def latest(self, order_reference: OrderReference) -> StoredProof | None: ...


@dataclass(frozen=True, slots=True)
class ProofBundleContext:
    certificate_reference: CertificateReference
    service_version: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.service_version) <= 64:
            raise ValueError("bundle_service_version_invalid")


class ProofBundler(Protocol):
    async def build(self, proof: StoredProof, receipt_json: bytes, context: ProofBundleContext) -> bytes: ...


def _validate_verification(value: BitcoinVerification) -> None:
    timestamps = (value.verified_at, value.block_time)
    if any(timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None for timestamp in timestamps):
        raise ValueError("verification_timestamps_must_be_timezone_aware")
    if value.block_height is None or value.block_height < 0:
        raise ValueError("verification_block_height_invalid")
    if value.block_hash is None or len(value.block_hash) != 64 or value.block_hash.lower() != value.block_hash:
        raise ValueError("verification_block_hash_invalid")
    try:
        bytes.fromhex(value.block_hash)
    except ValueError as error:
        raise ValueError("verification_block_hash_invalid") from error
    if not 1 <= len(value.method) <= 128 or not value.confirmation_policy or len(value.confirmation_policy) > 128:
        raise ValueError("verification_method_or_policy_invalid")
