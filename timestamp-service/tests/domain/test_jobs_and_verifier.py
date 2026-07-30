from __future__ import annotations

import pytest

from app.domain.digest import ManifestDigest
from app.jobs.models import BackoffPolicy
from app.ports.bitcoin import DisabledBitcoinVerifier


def test_backoff_is_exponential_bounded_and_jittered() -> None:
    policy = BackoffPolicy(base_seconds=10, maximum_seconds=30, jitter_ratio=0.2)
    assert policy.delay(1, 0).total_seconds() == 8
    assert policy.delay(2, 0.5).total_seconds() == 20
    assert policy.delay(10, 1).total_seconds() == 36


@pytest.mark.asyncio
async def test_disabled_verifier_can_never_confirm() -> None:
    result = await DisabledBitcoinVerifier().verify_exact_digest(ManifestDigest.from_hex("ab" * 32), b"proof")
    assert not result.verified
    assert result.block_hash is None
    assert result.method == "disabled"
