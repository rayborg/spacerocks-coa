from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app.domain.digest import ManifestDigest
from app.domain.identifiers import OrderReference
from app.ports.proof import MAX_PROOF_BYTES, ProofState, StoredProof
from app.proofs.store import InMemoryProofStore, make_stored_proof
from app.timestamping.fixture import FixtureTimestamper


@pytest.mark.asyncio
async def test_store_is_append_only_idempotent_and_checksum_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("bc" * 32)
    pending = await FixtureTimestamper().stamp_exact_digest(digest)
    order = OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB")
    proof = make_stored_proof(
        order,
        1,
        digest,
        pending.proof_bytes,
        proof_state=ProofState.CALENDAR_PENDING,
        calendar_submitted_at=pending.calendar_submitted_at,
        verification=None,
    )
    store = InMemoryProofStore()
    await store.append(proof)
    await store.append(proof)
    assert await store.versions(order) == (proof,)
    with pytest.raises(ValueError, match="proof_version_must_append"):
        await store.append(
            make_stored_proof(
                order,
                3,
                digest,
                pending.proof_bytes,
                proof_state=ProofState.CALENDAR_PENDING,
                calendar_submitted_at=pending.calendar_submitted_at,
                verification=None,
            )
        )


def test_stored_proof_enforces_shared_size_limit() -> None:
    values = {
        "order_reference": OrderReference("ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB"),
        "version": 1,
        "target_digest": ManifestDigest.from_hex("bc" * 32),
        "proof_state": ProofState.CALENDAR_PENDING,
        "calendar_submitted_at": datetime(2026, 7, 30, 12, 5, tzinfo=UTC),
        "verification": None,
    }
    at_limit = b"x" * MAX_PROOF_BYTES
    proof = StoredProof(
        **values,
        proof_bytes=at_limit,
        proof_sha256=hashlib.sha256(at_limit).digest(),
    )
    assert proof.proof_byte_length == MAX_PROOF_BYTES
    above_limit = b"x" * (MAX_PROOF_BYTES + 1)
    with pytest.raises(ValueError, match="proof_size_invalid"):
        StoredProof(
            **values,
            proof_bytes=above_limit,
            proof_sha256=b"0" * 32,
        )
