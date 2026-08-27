from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinEvidenceInvalid, BitcoinVerification, BitcoinVerifierUnavailable
from app.timestamping.detached import deserialize_exact_proof

MAX_RPC_RESPONSE_BYTES = 1_000_000
MAX_BITCOIN_ATTESTATIONS = 16
MINIMUM_SYNC_PROGRESS = 0.999


class BitcoinRpcTransport(Protocol):
    async def call(self, method: str, params: tuple[object, ...] = ()) -> object: ...


class BitcoinRpcResponseError(BitcoinEvidenceInvalid):
    def __init__(self, code: int, message: str) -> None:
        super().__init__("bitcoin_rpc_rejected_request")
        self.code = code
        self.rpc_message = message


class BitcoinCoreRpcTransport:
    """Bounded JSON-RPC transport for an operator-configured Bitcoin Core node."""

    def __init__(
        self,
        rpc_url: str,
        username: str,
        password: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if (
            not rpc_url
            or rpc_url != rpc_url.strip()
            or not rpc_url.isascii()
            or "\\" in rpc_url
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in rpc_url)
        ):
            raise ValueError("bitcoin_rpc_url_invalid")
        try:
            parsed = urlsplit(rpc_url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("bitcoin_rpc_url_invalid") from exc
        path_invalid = bool(parsed.path) and not parsed.path.startswith("/")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or path_invalid
            or (port is None and parsed.netloc.endswith(":"))
            or not username
            or not password
            or not 0.1 <= timeout_seconds <= 30.0
        ):
            raise ValueError("bitcoin_rpc_configuration_invalid")
        self._rpc_url = rpc_url
        self._auth = (username, password)
        self._timeout_seconds = timeout_seconds
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._request_ids = itertools.count(1)

    async def call(self, method: str, params: tuple[object, ...] = ()) -> object:
        if not method or not method.isascii() or len(method) > 64:
            raise ValueError("bitcoin_rpc_method_invalid")
        payload = {"jsonrpc": "2.0", "id": next(self._request_ids), "method": method, "params": params}
        status_code = 0
        body = bytearray()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    auth=self._auth,
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client:
                    async with client.stream("POST", self._rpc_url, json=payload) as response:
                        status_code = response.status_code
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RPC_RESPONSE_BYTES:
                                raise BitcoinEvidenceInvalid("bitcoin_rpc_response_too_large")
        except (BitcoinEvidenceInvalid, BitcoinVerifierUnavailable):
            raise
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise BitcoinVerifierUnavailable("bitcoin_rpc_unavailable") from exc
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if status_code >= 500:
                raise BitcoinVerifierUnavailable("bitcoin_rpc_http_unavailable") from exc
            raise BitcoinEvidenceInvalid("bitcoin_rpc_response_malformed") from exc
        if not isinstance(decoded, dict) or decoded.get("id") != payload["id"]:
            raise BitcoinEvidenceInvalid("bitcoin_rpc_response_malformed")
        error = decoded.get("error")
        if error is not None:
            if not isinstance(error, dict):
                raise BitcoinEvidenceInvalid("bitcoin_rpc_response_malformed")
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, bool) or not isinstance(code, int) or not isinstance(message, str):
                raise BitcoinEvidenceInvalid("bitcoin_rpc_response_malformed")
            if code in {-28, -342}:
                raise BitcoinVerifierUnavailable("bitcoin_rpc_not_ready")
            raise BitcoinRpcResponseError(code, message)
        if status_code != 200:
            raise BitcoinVerifierUnavailable("bitcoin_rpc_http_unavailable")
        if "result" not in decoded:
            raise BitcoinEvidenceInvalid("bitcoin_rpc_response_malformed")
        return decoded["result"]


@dataclass(frozen=True, slots=True)
class _ChainSnapshot:
    blocks: int
    best_block_hash: str


@dataclass(frozen=True, slots=True)
class _BlockEvidence:
    height: int
    block_hash: str
    block_time: datetime
    confirmations: int


class BitcoinCoreRpcVerifier:
    def __init__(
        self,
        rpc: BitcoinRpcTransport,
        *,
        minimum_confirmations: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(minimum_confirmations, bool) or not 1 <= minimum_confirmations <= 100:
            raise ValueError("bitcoin_confirmation_threshold_invalid")
        self._rpc = rpc
        self._minimum_confirmations = minimum_confirmations
        self._clock = clock or (lambda: datetime.now(UTC))

    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
        parsed = deserialize_exact_proof(digest, proof_bytes)
        attestations = {
            (message, attestation.height)
            for message, attestation in parsed.detached.timestamp.all_attestations()
            if type(attestation) is BitcoinBlockHeaderAttestation
        }
        if not attestations:
            return BitcoinVerification(verified=False, method="bitcoin-core-no-attestation")
        if len(attestations) > MAX_BITCOIN_ATTESTATIONS:
            raise BitcoinEvidenceInvalid("bitcoin_attestation_limit_exceeded")
        snapshot = await self._chain_snapshot()

        evidence: list[_BlockEvidence] = []
        pending_confirmations: list[int] = []
        for message, height in sorted(attestations, key=lambda item: (item[1], item[0])):
            if height > snapshot.blocks:
                pending_confirmations.append(0)
                continue
            block = await self._attestation_evidence(message, height, snapshot)
            if block.confirmations < self._minimum_confirmations:
                pending_confirmations.append(block.confirmations)
            else:
                evidence.append(block)
        final_snapshot = await self._chain_snapshot()
        if final_snapshot != snapshot:
            raise BitcoinVerifierUnavailable("bitcoin_chain_tip_changed")
        if not evidence:
            return BitcoinVerification(
                verified=False,
                method="bitcoin-core-confirmation-pending",
                confirmations=max(pending_confirmations, default=0),
            )

        strongest = max(evidence, key=lambda item: (item.confirmations, -item.height, item.block_hash))
        verified_at = self._clock()
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise BitcoinEvidenceInvalid("bitcoin_verifier_clock_invalid")
        return BitcoinVerification(
            verified=True,
            method="bitcoin-core-rpc-exact-digest",
            verified_at=verified_at.astimezone(UTC),
            block_height=strongest.height,
            block_hash=strongest.block_hash,
            block_time=strongest.block_time,
            confirmation_policy=f"bitcoin-core-mainnet-canonical-min-{self._minimum_confirmations}",
            confirmations=strongest.confirmations,
        )

    async def _chain_snapshot(self) -> _ChainSnapshot:
        result = await self._call("getblockchaininfo")
        if not isinstance(result, dict):
            raise BitcoinEvidenceInvalid("bitcoin_chain_info_malformed")
        chain = result.get("chain")
        if chain != "main":
            raise BitcoinEvidenceInvalid("bitcoin_wrong_network")
        blocks = _integer(result.get("blocks"), "bitcoin_chain_info_malformed", minimum=0)
        headers = _integer(result.get("headers"), "bitcoin_chain_info_malformed", minimum=0)
        best_block_hash = _hash_hex(result.get("bestblockhash"), "bitcoin_chain_info_malformed")
        progress = result.get("verificationprogress")
        if isinstance(progress, bool) or not isinstance(progress, int | float):
            raise BitcoinEvidenceInvalid("bitcoin_chain_info_malformed")
        if result.get("initialblockdownload") is not False or headers != blocks or progress < MINIMUM_SYNC_PROGRESS:
            raise BitcoinVerifierUnavailable("bitcoin_node_unsynchronized")
        return _ChainSnapshot(blocks=blocks, best_block_hash=best_block_hash)

    async def _attestation_evidence(
        self,
        message: bytes,
        height: int,
        snapshot: _ChainSnapshot,
    ) -> _BlockEvidence:
        if len(message) != 32 or isinstance(height, bool) or height < 0:
            raise BitcoinEvidenceInvalid("bitcoin_attestation_malformed")
        canonical_hash = _hash_hex(
            await self._chain_call("getblockhash", (height,)),
            "bitcoin_block_hash_malformed",
        )
        header = await self._chain_call("getblockheader", (canonical_hash, True))
        if not isinstance(header, dict):
            raise BitcoinEvidenceInvalid("bitcoin_block_header_malformed")
        if _integer(header.get("height"), "bitcoin_block_header_malformed", minimum=0) != height:
            raise BitcoinEvidenceInvalid("bitcoin_block_height_mismatch")
        if _hash_hex(header.get("hash"), "bitcoin_block_header_malformed") != canonical_hash:
            raise BitcoinEvidenceInvalid("bitcoin_block_hash_mismatch")
        confirmations = _integer(header.get("confirmations"), "bitcoin_block_header_malformed", minimum=-1)
        if confirmations < 0:
            raise BitcoinVerifierUnavailable("bitcoin_chain_race")
        expected_confirmations = snapshot.blocks - height + 1
        if confirmations != expected_confirmations:
            raise BitcoinVerifierUnavailable("bitcoin_confirmation_snapshot_mismatch")

        raw_header, merkle_root = _serialize_header(header, height)
        calculated_hash = hashlib.sha256(hashlib.sha256(raw_header).digest()).digest()[::-1].hex()
        if calculated_hash != canonical_hash:
            raise BitcoinEvidenceInvalid("bitcoin_block_header_hash_mismatch")
        if message != merkle_root:
            raise BitcoinEvidenceInvalid("bitcoin_attestation_merkle_mismatch")
        canonical_recheck = _hash_hex(
            await self._chain_call("getblockhash", (height,)),
            "bitcoin_block_hash_malformed",
        )
        if canonical_recheck != canonical_hash:
            raise BitcoinVerifierUnavailable("bitcoin_chain_race")

        timestamp = _integer(header.get("time"), "bitcoin_block_header_malformed", minimum=0, maximum=0xFFFFFFFF)
        try:
            block_time = datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise BitcoinEvidenceInvalid("bitcoin_block_time_invalid") from exc
        return _BlockEvidence(height, canonical_hash, block_time, expected_confirmations)

    async def _chain_call(self, method: str, params: tuple[object, ...]) -> object:
        try:
            return await self._call(method, params)
        except BitcoinRpcResponseError as exc:
            if exc.code in {-5, -8}:
                raise BitcoinVerifierUnavailable("bitcoin_chain_race") from exc
            raise

    async def _call(self, method: str, params: tuple[object, ...] = ()) -> object:
        try:
            return await self._rpc.call(method, params)
        except (BitcoinEvidenceInvalid, BitcoinVerifierUnavailable):
            raise
        except (OSError, TimeoutError) as exc:
            raise BitcoinVerifierUnavailable("bitcoin_rpc_unavailable") from exc


def _serialize_header(header: dict[object, object], height: int) -> tuple[bytes, bytes]:
    version = _integer(
        header.get("version"),
        "bitcoin_block_header_malformed",
        minimum=-(2**31),
        maximum=2**31 - 1,
    )
    previous = header.get("previousblockhash")
    if height == 0 and previous is None:
        previous_bytes = bytes(32)
    else:
        previous_bytes = bytes.fromhex(_hash_hex(previous, "bitcoin_block_header_malformed"))[::-1]
    merkle_root = bytes.fromhex(_hash_hex(header.get("merkleroot"), "bitcoin_block_header_malformed"))[::-1]
    timestamp = _integer(header.get("time"), "bitcoin_block_header_malformed", minimum=0, maximum=0xFFFFFFFF)
    bits_value = header.get("bits")
    if (
        not isinstance(bits_value, str)
        or len(bits_value) != 8
        or bits_value.lower() != bits_value
        or any(character not in "0123456789abcdef" for character in bits_value)
    ):
        raise BitcoinEvidenceInvalid("bitcoin_block_header_malformed")
    bits = int(bits_value, 16)
    nonce = _integer(header.get("nonce"), "bitcoin_block_header_malformed", minimum=0, maximum=0xFFFFFFFF)
    raw = struct.pack("<i", version) + previous_bytes + merkle_root + struct.pack("<III", timestamp, bits, nonce)
    return raw, merkle_root


def _integer(value: object, safe_code: str, *, minimum: int, maximum: int | None = None) -> int:
    too_large = isinstance(value, int) and maximum is not None and value > maximum
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or too_large:
        raise BitcoinEvidenceInvalid(safe_code)
    return value


def _hash_hex(value: object, safe_code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value.lower() != value:
        raise BitcoinEvidenceInvalid(safe_code)
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise BitcoinEvidenceInvalid(safe_code) from exc
    return value
