from __future__ import annotations

import hashlib
import json
import struct
from datetime import UTC, datetime

import httpx
import pytest
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend

from app.bitcoin.rpc import BitcoinCoreRpcTransport, BitcoinCoreRpcVerifier, BitcoinRpcResponseError
from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinEvidenceInvalid, BitcoinVerification, BitcoinVerifierUnavailable
from app.timestamping.detached import ProofValidationError, new_detached_exact_digest, serialize_detached

HEIGHT = 900_000
PREVIOUS_HASH = "11" * 32


def _proof(digest: ManifestDigest, height: int = HEIGHT) -> bytes:
    detached = new_detached_exact_digest(digest)
    detached.timestamp.attestations.add(BitcoinBlockHeaderAttestation(height))
    return serialize_detached(detached)


def _header(message: bytes, *, confirmations: int = 7) -> dict[str, object]:
    version = 0x20000000
    timestamp = 1_722_355_200
    bits = "170fffff"
    nonce = 2_345_678
    merkle_root = message[::-1].hex()
    raw = (
        struct.pack("<i", version)
        + bytes.fromhex(PREVIOUS_HASH)[::-1]
        + bytes.fromhex(merkle_root)[::-1]
        + struct.pack("<III", timestamp, int(bits, 16), nonce)
    )
    block_hash = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[::-1].hex()
    return {
        "hash": block_hash,
        "height": HEIGHT,
        "version": version,
        "previousblockhash": PREVIOUS_HASH,
        "merkleroot": merkle_root,
        "time": timestamp,
        "bits": bits,
        "nonce": nonce,
        "confirmations": confirmations,
    }


class FakeRpc:
    def __init__(
        self,
        header: dict[str, object],
        *,
        chain: str = "main",
        synchronized: bool = True,
        blocks: int = HEIGHT + 6,
        tip_results: list[tuple[int, str]] | None = None,
    ) -> None:
        self.header = header
        self.chain = chain
        self.synchronized = synchronized
        self.blocks = blocks
        self.tip_results = tip_results or [(blocks, "22" * 32)]
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.hash_results: list[str] = [str(header["hash"]), str(header["hash"])]

    async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
        self.calls.append((method, params))
        if method == "getblockchaininfo":
            blocks, best_block_hash = self.tip_results[0]
            if len(self.tip_results) > 1:
                self.tip_results.pop(0)
            return {
                "chain": self.chain,
                "blocks": blocks,
                "headers": blocks if self.synchronized else blocks + 1,
                "bestblockhash": best_block_hash,
                "initialblockdownload": False,
                "verificationprogress": 1.0,
            }
        if method == "getblockhash":
            return self.hash_results.pop(0)
        if method == "getblockheader":
            assert params == (self.header["hash"], True)
            return self.header
        raise AssertionError(f"unexpected RPC method {method}")


@pytest.mark.asyncio
async def test_valid_mainnet_header_verifies_exact_digest_and_exposes_confirmations() -> None:
    digest = ManifestDigest.from_hex("31" * 32)
    header = _header(digest.value, confirmations=7)
    rpc = FakeRpc(header)
    observed_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    result = await BitcoinCoreRpcVerifier(rpc, clock=lambda: observed_at).verify_exact_digest(digest, _proof(digest))
    assert result.verified
    assert result.confirmations == 7
    assert result.block_height == HEIGHT
    assert result.block_hash == header["hash"]
    assert result.block_time == datetime.fromtimestamp(int(header["time"]), UTC)
    assert result.verified_at == observed_at
    assert result.confirmation_policy == "bitcoin-core-mainnet-canonical-min-1"
    assert rpc.calls == [
        ("getblockchaininfo", ()),
        ("getblockhash", (HEIGHT,)),
        ("getblockheader", (header["hash"], True)),
        ("getblockhash", (HEIGHT,)),
        ("getblockchaininfo", ()),
    ]


@pytest.mark.asyncio
async def test_no_attestation_and_under_threshold_are_pending() -> None:
    digest = ManifestDigest.from_hex("32" * 32)
    pending = new_detached_exact_digest(digest)
    pending.timestamp.attestations.add(PendingAttestation("https://calendar.example/"))

    class MustNotCall:
        async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
            raise AssertionError((method, params))

    no_attestation = await BitcoinCoreRpcVerifier(MustNotCall()).verify_exact_digest(
        digest,
        serialize_detached(pending),
    )
    assert not no_attestation.verified
    assert no_attestation.confirmations is None

    rpc = FakeRpc(_header(digest.value, confirmations=7))
    result = await BitcoinCoreRpcVerifier(rpc, minimum_confirmations=8).verify_exact_digest(digest, _proof(digest))
    assert not result.verified
    assert result.confirmations == 7


@pytest.mark.asyncio
async def test_rpc_outage_and_unsynchronized_node_are_retryable() -> None:
    digest = ManifestDigest.from_hex("33" * 32)

    class Outage:
        async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
            del method, params
            raise OSError("connection refused")

    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_rpc_unavailable"):
        await BitcoinCoreRpcVerifier(Outage()).verify_exact_digest(digest, _proof(digest))
    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_node_unsynchronized"):
        await BitcoinCoreRpcVerifier(FakeRpc(_header(digest.value), synchronized=False)).verify_exact_digest(
            digest, _proof(digest)
        )


@pytest.mark.asyncio
async def test_wrong_network_merkle_header_hash_and_reorg_never_verify() -> None:
    digest = ManifestDigest.from_hex("34" * 32)
    proof = _proof(digest)
    wrong_network = FakeRpc(_header(digest.value), chain="test")
    with pytest.raises(BitcoinEvidenceInvalid, match="bitcoin_wrong_network"):
        await BitcoinCoreRpcVerifier(wrong_network).verify_exact_digest(digest, proof)

    wrong_merkle_header = _header(b"x" * 32)
    with pytest.raises(BitcoinEvidenceInvalid, match="bitcoin_attestation_merkle_mismatch"):
        await BitcoinCoreRpcVerifier(FakeRpc(wrong_merkle_header)).verify_exact_digest(digest, proof)

    malformed_hash = _header(digest.value)
    malformed_hash["nonce"] = int(malformed_hash["nonce"]) + 1
    with pytest.raises(BitcoinEvidenceInvalid, match="bitcoin_block_header_hash_mismatch"):
        await BitcoinCoreRpcVerifier(FakeRpc(malformed_hash)).verify_exact_digest(digest, proof)

    reorg = FakeRpc(_header(digest.value))
    reorg.hash_results[-1] = "44" * 32
    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_chain_race"):
        await BitcoinCoreRpcVerifier(reorg).verify_exact_digest(digest, proof)


@pytest.mark.asyncio
async def test_future_height_is_pending_and_wrong_target_is_invalid() -> None:
    digest = ManifestDigest.from_hex("35" * 32)

    class FutureHeight(FakeRpc):
        async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
            if method == "getblockchaininfo":
                return {
                    "chain": "main",
                    "blocks": HEIGHT - 1,
                    "headers": HEIGHT - 1,
                    "bestblockhash": "22" * 32,
                    "initialblockdownload": False,
                    "verificationprogress": 1.0,
                }
            if method != "getblockchaininfo":
                raise AssertionError((method, params))

    result = await BitcoinCoreRpcVerifier(FutureHeight(_header(digest.value))).verify_exact_digest(
        digest,
        _proof(digest),
    )
    assert not result.verified
    assert result.confirmations == 0

    with pytest.raises(ProofValidationError, match="proof_target_mismatch"):
        await BitcoinCoreRpcVerifier(FakeRpc(_header(digest.value))).verify_exact_digest(
            ManifestDigest.from_hex("36" * 32),
            _proof(digest),
        )


def test_verified_result_without_positive_confirmation_count_is_rejected() -> None:
    metadata = {
        "verified": True,
        "method": "test",
        "verified_at": datetime(2026, 8, 27, tzinfo=UTC),
        "block_height": HEIGHT,
        "block_hash": "11" * 32,
        "block_time": datetime(2026, 8, 27, tzinfo=UTC),
        "confirmation_policy": "test-policy",
    }
    with pytest.raises(ValueError, match="at least one confirmation"):
        BitcoinVerification(**metadata)
    with pytest.raises(ValueError, match="at least one confirmation"):
        BitcoinVerification(**metadata, confirmations=0)


@pytest.mark.asyncio
async def test_six_confirmation_milestone_uses_stable_snapshot_count() -> None:
    digest = ManifestDigest.from_hex("37" * 32)
    five_confirmation_rpc = FakeRpc(_header(digest.value, confirmations=5), blocks=HEIGHT + 4)
    pending = await BitcoinCoreRpcVerifier(five_confirmation_rpc, minimum_confirmations=6).verify_exact_digest(
        digest,
        _proof(digest),
    )
    assert not pending.verified
    assert pending.confirmations == 5

    rpc = FakeRpc(_header(digest.value, confirmations=6), blocks=HEIGHT + 5)
    result = await BitcoinCoreRpcVerifier(rpc, minimum_confirmations=6).verify_exact_digest(digest, _proof(digest))
    assert result.verified
    assert result.confirmations == 6
    assert rpc.calls[-1] == ("getblockchaininfo", ())


@pytest.mark.asyncio
async def test_snapshot_confirmation_mismatch_and_tip_change_are_retryable() -> None:
    digest = ManifestDigest.from_hex("38" * 32)
    mismatch = FakeRpc(_header(digest.value, confirmations=6))
    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_confirmation_snapshot_mismatch"):
        await BitcoinCoreRpcVerifier(mismatch).verify_exact_digest(digest, _proof(digest))

    changed_tip = FakeRpc(
        _header(digest.value, confirmations=7),
        tip_results=[(HEIGHT + 6, "22" * 32), (HEIGHT + 7, "33" * 32)],
    )
    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_chain_tip_changed"):
        await BitcoinCoreRpcVerifier(changed_tip).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
@pytest.mark.parametrize("rpc_code", (-5, -8))
async def test_chain_race_rpc_errors_are_retryable(rpc_code: int) -> None:
    digest = ManifestDigest.from_hex("39" * 32)

    class ChainRaceRpc(FakeRpc):
        async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
            if method == "getblockheader":
                raise BitcoinRpcResponseError(rpc_code, "Block not available during chain update")
            return await super().call(method, params)

    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_chain_race"):
        await BitcoinCoreRpcVerifier(ChainRaceRpc(_header(digest.value))).verify_exact_digest(
            digest,
            _proof(digest),
        )


@pytest.mark.asyncio
async def test_valid_bitcoin_branch_verifies_while_parallel_calendar_branch_remains_pending() -> None:
    digest = ManifestDigest.from_hex("3a" * 32)
    detached = new_detached_exact_digest(digest)
    detached.timestamp.attestations.add(BitcoinBlockHeaderAttestation(HEIGHT))
    pending_branch = detached.timestamp.ops.add(OpAppend(b"parallel-calendar-branch"))
    pending_branch.attestations.add(PendingAttestation("https://calendar.example/"))

    result = await BitcoinCoreRpcVerifier(FakeRpc(_header(digest.value))).verify_exact_digest(
        digest,
        serialize_detached(detached),
    )
    assert result.verified
    assert result.confirmations == 7


@pytest.mark.asyncio
async def test_rpc_transport_is_mockable_bounded_and_parses_core_error_statuses() -> None:
    observed: list[dict[str, object]] = []

    def core_error(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append(payload)
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            500,
            json={"result": None, "error": {"code": -8, "message": "Block height out of range"}, "id": payload["id"]},
        )

    transport = BitcoinCoreRpcTransport(
        "http://bitcoin-core.internal:8332/",
        "rpc-user",
        "rpc-password",
        transport=httpx.MockTransport(core_error),
    )
    with pytest.raises(BitcoinRpcResponseError) as raised:
        await transport.call("getblockhash", (HEIGHT,))
    assert raised.value.code == -8
    assert observed == [{"jsonrpc": "2.0", "id": 1, "method": "getblockhash", "params": [HEIGHT]}]

    unavailable = BitcoinCoreRpcTransport(
        "http://bitcoin-core.internal:8332/",
        "rpc-user",
        "rpc-password",
        transport=httpx.MockTransport(lambda request: httpx.Response(503, content=b"temporarily unavailable")),
    )
    with pytest.raises(BitcoinVerifierUnavailable, match="bitcoin_rpc_http_unavailable"):
        await unavailable.call("getblockchaininfo")
