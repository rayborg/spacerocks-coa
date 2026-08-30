from __future__ import annotations

import pytest

from app.bitcoin.disabled import DisabledVerifier
from app.bitcoin.fixture import FixtureBitcoinVerifier
from app.domain.digest import ManifestDigest
from app.timestamping.fixture import FixtureTimestamper


@pytest.mark.asyncio
async def test_pending_fixture_and_disabled_verifier_never_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("56" * 32)
    proof = await FixtureTimestamper().stamp_exact_digest(digest)
    assert not (await FixtureBitcoinVerifier().verify_exact_digest(digest, proof.proof_bytes)).verified
    assert not (await DisabledVerifier().verify_exact_digest(digest, proof.proof_bytes)).verified


@pytest.mark.asyncio
async def test_fixture_confirmation_requires_attestation_and_exact_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    digest = ManifestDigest.from_hex("78" * 32)
    timestamper = FixtureTimestamper(confirm_on_upgrade=True)
    pending = await timestamper.stamp_exact_digest(digest)
    upgraded = await timestamper.upgrade_exact_digest(digest, pending.proof_bytes)
    result = await FixtureBitcoinVerifier().verify_exact_digest(digest, upgraded)
    assert result.verified
    assert result.block_height == 900_000
    assert result.confirmations == 6
    with pytest.raises(ValueError, match="proof_target_mismatch"):
        await FixtureBitcoinVerifier().verify_exact_digest(ManifestDigest.from_hex("79" * 32), upgraded)


def test_fixture_verifier_cannot_initialize_outside_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="fixture_adapter_forbidden"):
        FixtureBitcoinVerifier()
