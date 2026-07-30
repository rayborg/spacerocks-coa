from __future__ import annotations

from app.ports.proof import ProofBundler
from app.proofs.bundle import DeterministicProofBundler


def create_proof_bundler() -> ProofBundler:
    return DeterministicProofBundler()
