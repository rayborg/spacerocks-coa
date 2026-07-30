from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpPrepend, OpSHA256
from opentimestamps.core.serialize import BytesDeserializationContext, BytesSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from app.domain.digest import ManifestDigest
from app.ports.proof import MAX_PROOF_BYTES

MAX_TIMESTAMP_NODES = 256
MAX_OPERATIONS = 255
MAX_ATTESTATIONS = 256
MAX_PENDING_ATTESTATIONS = 16
MAX_OPERATION_ARGUMENT_BYTES = 64


class ProofValidationError(ValueError):
    """A detached proof failed a customer-safe structural or target check."""


@dataclass(frozen=True, slots=True)
class ParsedProof:
    detached: DetachedTimestampFile
    node_count: int
    operation_count: int
    attestation_count: int
    pending_attestation_count: int


def new_detached_exact_digest(digest: ManifestDigest) -> DetachedTimestampFile:
    """Construct a SHA-256 detached file around the supplied digest, without hashing it."""
    return DetachedTimestampFile(OpSHA256(), Timestamp(digest.ots_target()))


def serialize_detached(detached: DetachedTimestampFile) -> bytes:
    validate_timestamp_tree(detached.timestamp)
    context = BytesSerializationContext()
    detached.serialize(context)
    proof_bytes = cast(bytes, context.getbytes())
    if not proof_bytes or len(proof_bytes) > MAX_PROOF_BYTES:
        raise ProofValidationError("proof_size_invalid")
    return proof_bytes


def deserialize_exact_proof(
    digest: ManifestDigest,
    proof_bytes: bytes,
    *,
    expected_sha256: bytes | None = None,
) -> ParsedProof:
    import hashlib

    proof_bytes = bytes(proof_bytes)
    if not proof_bytes or len(proof_bytes) > MAX_PROOF_BYTES:
        raise ProofValidationError("proof_size_invalid")
    if expected_sha256 is not None:
        if len(expected_sha256) != 32 or hashlib.sha256(proof_bytes).digest() != expected_sha256:
            raise ProofValidationError("proof_checksum_mismatch")
    try:
        detached = DetachedTimestampFile.deserialize(BytesDeserializationContext(proof_bytes))
    except Exception as exc:
        raise ProofValidationError("proof_deserialization_failed") from exc
    if type(detached.file_hash_op) is not OpSHA256:
        raise ProofValidationError("proof_hash_operation_mismatch")
    if detached.file_digest != digest.ots_target():
        raise ProofValidationError("proof_target_mismatch")

    counts = validate_timestamp_tree(detached.timestamp)
    return ParsedProof(
        detached=detached,
        node_count=counts[0],
        operation_count=counts[1],
        attestation_count=counts[2],
        pending_attestation_count=counts[3],
    )


def validate_timestamp_tree(root: Timestamp) -> tuple[int, int, int, int]:
    node_count = 0
    operation_count = 0
    attestation_count = 0
    pending_attestation_count = 0
    pending = [root]
    while pending:
        timestamp = pending.pop()
        node_count += 1
        if node_count > MAX_TIMESTAMP_NODES:
            raise ProofValidationError("proof_node_limit_exceeded")
        for attestation in timestamp.attestations:
            attestation_count += 1
            if attestation_count > MAX_ATTESTATIONS:
                raise ProofValidationError("proof_attestation_limit_exceeded")
            if type(attestation) is PendingAttestation:
                pending_attestation_count += 1
                if pending_attestation_count > MAX_PENDING_ATTESTATIONS:
                    raise ProofValidationError("proof_pending_attestation_limit_exceeded")
            elif type(attestation) is not BitcoinBlockHeaderAttestation:
                raise ProofValidationError("proof_attestation_type_unsupported")
        for operation, child in timestamp.ops.items():
            operation_count += 1
            if operation_count > MAX_OPERATIONS:
                raise ProofValidationError("proof_operation_limit_exceeded")
            if type(operation) not in {OpAppend, OpPrepend, OpSHA256}:
                raise ProofValidationError("proof_operation_unsupported")
            if type(operation) in {OpAppend, OpPrepend}:
                argument = operation[0]
                if not 1 <= len(argument) <= MAX_OPERATION_ARGUMENT_BYTES:
                    raise ProofValidationError("proof_operation_argument_invalid")
            if child.msg != operation(timestamp.msg):
                raise ProofValidationError("proof_operation_result_mismatch")
            pending.append(child)
    return node_count, operation_count, attestation_count, pending_attestation_count


def validate_exact_proof(
    digest: ManifestDigest,
    proof_bytes: bytes,
    *,
    expected_sha256: bytes | None = None,
) -> None:
    deserialize_exact_proof(digest, proof_bytes, expected_sha256=expected_sha256)
