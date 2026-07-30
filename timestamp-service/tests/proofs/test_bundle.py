from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from app.domain.digest import ManifestDigest
from app.domain.identifiers import CertificateReference, OrderReference
from app.ports.bitcoin import BitcoinVerification
from app.ports.proof import ProofBundleContext, ProofState
from app.proofs.bundle import DeterministicProofBundler, safe_bundle_filename
from app.proofs.factory import create_proof_bundler
from app.proofs.receipt import ReceiptInput, build_receipt
from app.proofs.store import make_stored_proof
from app.timestamping.fixture import FixtureTimestamper

CONTEXT = ProofBundleContext(
    certificate_reference=CertificateReference("AZ-2019-0447-HE"),
    service_version="phase0-test",
)


@pytest.mark.asyncio
async def test_bundle_is_deterministic_schema_valid_private_and_checksum_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("9a" * 32)
    pending = await FixtureTimestamper().stamp_exact_digest(digest)
    proof = make_stored_proof(
        OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        1,
        digest,
        pending.proof_bytes,
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=pending.calendar_submitted_at,
        verification=None,
    )
    receipt = build_receipt(
        ReceiptInput(
            proof=proof,
            certificate_reference=CertificateReference("AZ-2019-0447-HE"),
            service_version="phase0-test",
        )
    )
    schema_path = Path(__file__).resolve().parents[3] / "contracts/schemas/timestamp-receipt.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).validate(json.loads(receipt))

    bundler = DeterministicProofBundler()
    first = await bundler.build(proof, receipt, CONTEXT)
    second = await bundler.build(proof, receipt, CONTEXT)
    assert first == second
    assert b"customer@example.com" not in first
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "README-FIRST.txt",
            "manifest.json.ots",
            "timestamp-receipt.json",
            "verification-instructions.txt",
            "sha256sums.txt",
        ]
        assert b"PENDING" in archive.read("README-FIRST.txt")
        sums = archive.read("sha256sums.txt").decode()
        for line in sums.splitlines():
            checksum, name = line.split("  ", 1)
            import hashlib

            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum


def test_bundle_filename_cannot_traverse_paths() -> None:
    filename = safe_bundle_filename("../../private/token:certificate")
    assert "/" not in filename
    assert ".." not in filename
    assert filename.endswith("-bitcoin-timestamp.zip")


def test_api_composition_factory_is_strict_bundler() -> None:
    assert isinstance(create_proof_bundler(), DeterministicProofBundler)


@pytest.mark.asyncio
async def test_receipt_state_time_and_verification_are_exactly_proof_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("9b" * 32)
    pending = await FixtureTimestamper().stamp_exact_digest(digest)
    proof = make_stored_proof(
        OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        1,
        digest,
        pending.proof_bytes,
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=pending.calendar_submitted_at,
        verification=None,
    )
    receipt = json.loads(
        build_receipt(
            ReceiptInput(
                proof=proof,
                certificate_reference=CertificateReference("AZ-2019-0447-HE"),
                service_version="phase0-test",
            )
        )
    )
    forged = dict(receipt)
    forged.update(
        {
            "proof_state": "bitcoin_verified",
            "bitcoin": {
                "block_height": 900000,
                "block_hash": "ab" * 32,
                "block_time": "2026-07-30T15:00:00Z",
                "confirmation_policy": "forged",
            },
            "verification_method": "forged",
            "verified_at": "2026-07-30T15:10:00Z",
        }
    )
    bundler = DeterministicProofBundler()
    with pytest.raises(ValueError, match="receipt_proof_state_mismatch"):
        await bundler.build(proof, json.dumps(forged).encode(), CONTEXT)

    altered_time = dict(receipt)
    altered_time["calendar_submitted_at"] = "2026-07-30T12:05:01Z"
    with pytest.raises(ValueError, match="receipt_calendar_time_mismatch"):
        await bundler.build(proof, json.dumps(altered_time).encode(), CONTEXT)
    same_instant = dict(receipt)
    same_instant["calendar_submitted_at"] = "2026-07-30T08:05:00-04:00"
    await bundler.build(proof, json.dumps(same_instant).encode(), CONTEXT)

    altered_certificate = dict(receipt)
    altered_certificate["certificate_reference"] = "FORGED-CERTIFICATE"
    with pytest.raises(ValueError, match="receipt_certificate_reference_mismatch"):
        await bundler.build(proof, json.dumps(altered_certificate).encode(), CONTEXT)
    altered_service = dict(receipt)
    altered_service["service_version"] = "forged-service"
    with pytest.raises(ValueError, match="receipt_service_version_mismatch"):
        await bundler.build(proof, json.dumps(altered_service).encode(), CONTEXT)

    verification = BitcoinVerification(
        verified=True,
        method="fixture-exact-digest",
        verified_at=datetime(2026, 7, 30, 15, 10, tzinfo=UTC),
        block_height=900000,
        block_hash="ab" * 32,
        block_time=datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
        confirmation_policy="phase0-fixture-exact-target",
    )
    verified_proof = make_stored_proof(
        proof.order_reference,
        2,
        digest,
        proof.proof_bytes,
        proof_state=ProofState.BITCOIN_VERIFIED,
        calendar_submitted_at=proof.calendar_submitted_at,
        verification=verification,
    )
    verified_receipt = json.loads(
        build_receipt(
            ReceiptInput(
                proof=verified_proof,
                certificate_reference=CertificateReference("AZ-2019-0447-HE"),
                service_version="phase0-test",
            )
        )
    )
    for field, altered in (
        ("block_height", 900001),
        ("block_hash", "cd" * 32),
        ("block_time", "2026-07-30T15:00:01Z"),
        ("confirmation_policy", "altered"),
    ):
        hostile = json.loads(json.dumps(verified_receipt))
        hostile["bitcoin"][field] = altered
        with pytest.raises(ValueError, match="receipt_verification_metadata_mismatch"):
            await bundler.build(verified_proof, json.dumps(hostile).encode(), CONTEXT)
    for field, altered in (("verification_method", "altered"), ("verified_at", "2026-07-30T15:10:01Z")):
        hostile = json.loads(json.dumps(verified_receipt))
        hostile[field] = altered
        with pytest.raises(ValueError, match="receipt_verification_metadata_mismatch"):
            await bundler.build(verified_proof, json.dumps(hostile).encode(), CONTEXT)
