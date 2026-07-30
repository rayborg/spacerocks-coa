from __future__ import annotations

import hashlib

import pytest
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpHexlify, OpKECCAK256
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from app.domain.digest import ManifestDigest
from app.timestamping.detached import (
    MAX_PROOF_BYTES,
    ProofValidationError,
    deserialize_exact_proof,
    new_detached_exact_digest,
    serialize_detached,
)
from app.timestamping.fixture import DisabledTimestamper, FixtureTimestamper


@pytest.mark.asyncio
async def test_fixture_targets_original_digest_not_hash_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("cf0a31b01661599b8f73cd2dd2830f859e36a00c8ca22b259b33a7ec32c067cc")
    proof = await FixtureTimestamper().stamp_exact_digest(digest)

    parsed = deserialize_exact_proof(digest, proof.proof_bytes)
    assert parsed.detached.file_digest == digest.value
    with pytest.raises(ProofValidationError, match="proof_target_mismatch"):
        hash_of_hex = ManifestDigest.from_bytes(hashlib.sha256(digest.hex.encode()).digest())
        deserialize_exact_proof(hash_of_hex, proof.proof_bytes)
    with pytest.raises(ProofValidationError, match="proof_target_mismatch"):
        deserialize_exact_proof(ManifestDigest.from_bytes(hashlib.sha256(digest.value).digest()), proof.proof_bytes)


def test_fixture_is_forbidden_outside_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="fixture_adapter_forbidden"):
        FixtureTimestamper()


@pytest.mark.asyncio
async def test_disabled_timestamper_has_no_network_or_success_path() -> None:
    with pytest.raises(RuntimeError, match="timestamping_disabled"):
        await DisabledTimestamper().stamp_exact_digest(ManifestDigest.from_hex("ab" * 32))


def test_wrong_operation_corrupt_truncated_oversized_and_checksum_are_rejected() -> None:
    digest = ManifestDigest.from_hex("ab" * 32)
    timestamp = Timestamp(digest.value)
    timestamp.attestations.add(PendingAttestation("https://fixture.invalid/"))
    wrong_operation = serialize_detached(DetachedTimestampFile(OpKECCAK256(), timestamp))
    with pytest.raises(ProofValidationError, match="proof_hash_operation_mismatch"):
        deserialize_exact_proof(digest, wrong_operation)

    for invalid in (b"not-an-ots", wrong_operation[:-1], b"x" * (MAX_PROOF_BYTES + 1)):
        with pytest.raises(ProofValidationError):
            deserialize_exact_proof(digest, invalid)
    with pytest.raises(ProofValidationError, match="proof_checksum_mismatch"):
        deserialize_exact_proof(digest, wrong_operation, expected_sha256=b"0" * 32)


def _raw_serialize(detached: DetachedTimestampFile) -> bytes:
    context = BytesSerializationContext()
    detached.serialize(context)
    return context.getbytes()


def test_nested_unsupported_operation_and_complexity_caps_are_rejected() -> None:
    digest = ManifestDigest.from_hex("ac" * 32)
    unsupported = new_detached_exact_digest(digest)
    child = unsupported.timestamp.ops.add(OpHexlify())
    child.attestations.add(PendingAttestation("https://fixture.invalid/"))
    with pytest.raises(ProofValidationError, match="proof_operation_unsupported"):
        deserialize_exact_proof(digest, _raw_serialize(unsupported))

    pending_fanout = new_detached_exact_digest(digest)
    pending_fanout.timestamp.attestations.update(
        PendingAttestation(f"https://calendar-{index}.invalid/") for index in range(17)
    )
    with pytest.raises(ProofValidationError, match="proof_pending_attestation_limit_exceeded"):
        deserialize_exact_proof(digest, _raw_serialize(pending_fanout))

    attestations = new_detached_exact_digest(digest)
    attestations.timestamp.attestations.update(BitcoinBlockHeaderAttestation(index) for index in range(257))
    with pytest.raises(ProofValidationError, match="proof_attestation_limit_exceeded"):
        deserialize_exact_proof(digest, _raw_serialize(attestations))

    operations = new_detached_exact_digest(digest)
    for index in range(256):
        branch = operations.timestamp.ops.add(OpAppend(index.to_bytes(2)))
        branch.attestations.add(BitcoinBlockHeaderAttestation(index))
    with pytest.raises(ProofValidationError, match="proof_operation_limit_exceeded"):
        deserialize_exact_proof(digest, _raw_serialize(operations))


def test_validator_probe_with_fifty_thousand_attestations_fails_closed() -> None:
    digest = ManifestDigest.from_hex("ad" * 32)
    hostile = new_detached_exact_digest(digest)
    hostile.timestamp.attestations.update(BitcoinBlockHeaderAttestation(index) for index in range(50_000))
    raw = _raw_serialize(hostile)
    with pytest.raises(ProofValidationError, match="proof_size_invalid|proof_attestation_limit_exceeded"):
        deserialize_exact_proof(digest, raw)
