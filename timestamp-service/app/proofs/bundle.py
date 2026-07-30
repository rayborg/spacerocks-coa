from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime

from app.ports.proof import ProofBundleContext, StoredProof
from app.timestamping.detached import validate_exact_proof

_FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "order_reference",
        "certificate_reference",
        "manifest_sha256",
        "proof_sha256",
        "proof_bytes",
        "proof_state",
        "calendar_submitted_at",
        "service_version",
        "bitcoin",
        "verification_method",
        "verified_at",
    }
)
_ORDER_REFERENCE = re.compile(r"^ts_[0-9A-HJKMNP-TV-Z]{26}$")
_CERTIFICATE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def safe_bundle_filename(certificate_reference: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", certificate_reference).strip("._-")
    safe = safe[:96] or "certificate"
    return f"{safe}-bitcoin-timestamp.zip"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _checksum_line(name: str, content: bytes) -> str:
    return f"{hashlib.sha256(content).hexdigest()}  {name}\n"


def _date_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _validate_receipt(
    receipt: object,
    proof: StoredProof,
    context: ProofBundleContext,
) -> dict[str, object]:
    if not isinstance(receipt, dict) or not all(isinstance(key, str) for key in receipt):
        raise ValueError("receipt_schema_fields_invalid")
    typed = {str(key): value for key, value in receipt.items()}
    if not set(typed).issubset(_RECEIPT_KEYS):
        raise ValueError("receipt_schema_fields_invalid")
    required = {
        "schema_version",
        "order_reference",
        "certificate_reference",
        "manifest_sha256",
        "proof_sha256",
        "proof_bytes",
        "proof_state",
        "calendar_submitted_at",
        "service_version",
    }
    if not required.issubset(typed):
        raise ValueError("receipt_schema_fields_missing")
    order_reference = typed["order_reference"]
    certificate_reference = typed["certificate_reference"]
    service_version = typed["service_version"]
    if (
        typed["schema_version"] != "1.0.0"
        or not isinstance(order_reference, str)
        or not _ORDER_REFERENCE.fullmatch(order_reference)
        or not isinstance(certificate_reference, str)
        or not _CERTIFICATE_REFERENCE.fullmatch(certificate_reference)
        or not isinstance(service_version, str)
        or not 1 <= len(service_version) <= 64
        or _date_time(typed["calendar_submitted_at"]) is None
    ):
        raise ValueError("receipt_schema_values_invalid")
    if certificate_reference != context.certificate_reference.value:
        raise ValueError("receipt_certificate_reference_mismatch")
    if service_version != context.service_version:
        raise ValueError("receipt_service_version_mismatch")
    proof_bytes = typed["proof_bytes"]
    if (
        typed["order_reference"] != proof.order_reference.value
        or typed["manifest_sha256"] != proof.target_digest.hex
        or typed["proof_sha256"] != proof.proof_sha256.hex()
        or type(proof_bytes) is not int
        or proof_bytes != len(proof.proof_bytes)
    ):
        raise ValueError("receipt_proof_binding_mismatch")
    for digest_field in ("manifest_sha256", "proof_sha256"):
        digest_value = typed[digest_field]
        if not isinstance(digest_value, str) or not _SHA256_HEX.fullmatch(digest_value):
            raise ValueError("receipt_digest_invalid")

    state = typed["proof_state"]
    receipt_calendar_time = _date_time(typed["calendar_submitted_at"])
    if state != proof.proof_state.value:
        raise ValueError("receipt_proof_state_mismatch")
    if receipt_calendar_time != proof.calendar_submitted_at.astimezone(UTC):
        raise ValueError("receipt_calendar_time_mismatch")
    confirmation_fields = {"bitcoin", "verification_method", "verified_at"}
    if state == "calendar_pending" and confirmation_fields.intersection(typed):
        raise ValueError("pending_receipt_cannot_claim_confirmation")
    if state == "calendar_pending" and proof.verification is not None:
        raise ValueError("pending_proof_cannot_carry_verification")
    if state == "bitcoin_verified":
        if not confirmation_fields.issubset(typed):
            raise ValueError("verified_receipt_metadata_missing")
        bitcoin = typed["bitcoin"]
        if not isinstance(bitcoin, dict) or set(bitcoin) != {
            "block_height",
            "block_hash",
            "block_time",
            "confirmation_policy",
        }:
            raise ValueError("bitcoin_receipt_metadata_invalid")
        height = bitcoin["block_height"]
        block_hash = bitcoin["block_hash"]
        policy = bitcoin["confirmation_policy"]
        method = typed["verification_method"]
        if (
            type(height) is not int
            or height < 0
            or not isinstance(block_hash, str)
            or not _SHA256_HEX.fullmatch(block_hash)
            or _date_time(bitcoin["block_time"]) is None
            or not isinstance(policy, str)
            or not 1 <= len(policy) <= 128
            or not isinstance(method, str)
            or not 1 <= len(method) <= 128
            or _date_time(typed["verified_at"]) is None
        ):
            raise ValueError("bitcoin_receipt_metadata_invalid")
        verification = proof.verification
        verification_block_time = verification.block_time if verification is not None else None
        verification_verified_at = verification.verified_at if verification is not None else None
        if (
            verification is None
            or verification_block_time is None
            or verification_verified_at is None
            or height != verification.block_height
            or block_hash != verification.block_hash
            or policy != verification.confirmation_policy
            or method != verification.method
            or _date_time(bitcoin["block_time"]) != verification_block_time.astimezone(UTC)
            or _date_time(typed["verified_at"]) != verification_verified_at.astimezone(UTC)
        ):
            raise ValueError("receipt_verification_metadata_mismatch")
    elif state != "calendar_pending":
        raise ValueError("receipt_proof_state_invalid")
    if "@" in json.dumps(typed, sort_keys=True).lower():
        raise ValueError("receipt_private_data_forbidden")
    return typed


class DeterministicProofBundler:
    async def build(self, proof: StoredProof, receipt_json: bytes, context: ProofBundleContext) -> bytes:
        validate_exact_proof(proof.target_digest, proof.proof_bytes, expected_sha256=proof.proof_sha256)
        if not receipt_json or len(receipt_json) > 64 * 1024:
            raise ValueError("receipt_json_size_invalid")
        try:
            receipt = json.loads(receipt_json)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("receipt_json_invalid") from exc
        receipt = _validate_receipt(receipt, proof, context)

        pending = receipt["proof_state"] == "calendar_pending"
        readme = (
            "Spacerocks supplemental OpenTimestamps proof\n\n"
            "This package is separate from the original signed COA. It proves only that the exact\n"
            "manifest SHA-256 digest was timestamped; it does not prove identity, ownership, or authenticity.\n"
            + (
                "Status: PENDING. Calendar submission is not Bitcoin confirmation.\n"
                if pending
                else "Status: BITCOIN VERIFIED under the policy recorded in timestamp-receipt.json.\n"
            )
        ).encode("ascii")
        instructions = (
            "Keep the original manifest.json and manifest.json.ots together.\n"
            "Verify sha256sums.txt, then use an independent OpenTimestamps client to verify the proof.\n"
            "A pending calendar attestation must not be treated as Bitcoin confirmation.\n"
        ).encode("ascii")
        files = {
            "README-FIRST.txt": readme,
            "manifest.json.ots": proof.proof_bytes,
            "timestamp-receipt.json": bytes(receipt_json),
            "verification-instructions.txt": instructions,
        }
        sums = "".join(_checksum_line(name, files[name]) for name in sorted(files)).encode("ascii")
        files["sha256sums.txt"] = sums

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
            for name in (
                "README-FIRST.txt",
                "manifest.json.ots",
                "timestamp-receipt.json",
                "verification-instructions.txt",
                "sha256sums.txt",
            ):
                archive.writestr(_zip_info(name), files[name])
        bundle = output.getvalue()
        if len(bundle) > 12 * 1024 * 1024:
            raise ValueError("proof_bundle_too_large")
        return bundle
