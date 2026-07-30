from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.identifiers import CertificateReference
from app.ports.proof import StoredProof


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("receipt_timestamps_must_be_timezone_aware")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReceiptInput:
    proof: StoredProof
    certificate_reference: CertificateReference
    service_version: str


def build_receipt(value: ReceiptInput) -> bytes:
    if not 1 <= len(value.service_version) <= 64:
        raise ValueError("invalid_service_version")
    proof_state = value.proof.proof_state.value
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "order_reference": value.proof.order_reference.value,
        "certificate_reference": value.certificate_reference.value,
        "manifest_sha256": value.proof.target_digest.hex,
        "proof_sha256": value.proof.proof_sha256.hex(),
        "proof_bytes": len(value.proof.proof_bytes),
        "proof_state": proof_state,
        "calendar_submitted_at": _rfc3339(value.proof.calendar_submitted_at),
        "service_version": value.service_version,
    }
    if value.proof.verification is not None:
        bitcoin = value.proof.verification
        if (
            not bitcoin.verified
            or bitcoin.block_height is None
            or bitcoin.block_hash is None
            or bitcoin.block_time is None
            or bitcoin.confirmation_policy is None
            or bitcoin.verified_at is None
        ):
            raise ValueError("verified_receipt_metadata_incomplete")
        receipt["bitcoin"] = {
            "block_height": bitcoin.block_height,
            "block_hash": bitcoin.block_hash,
            "block_time": _rfc3339(bitcoin.block_time),
            "confirmation_policy": bitcoin.confirmation_policy,
        }
        receipt["verification_method"] = bitcoin.method
        receipt["verified_at"] = _rfc3339(bitcoin.verified_at)
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
