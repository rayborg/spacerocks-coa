from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinEvidenceInvalid, BitcoinVerification, BitcoinVerifierUnavailable
from app.timestamping.detached import deserialize_exact_proof

ESPLORA_RESPONSE_LIMIT = 4_096
MAX_BITCOIN_CANDIDATES = 16
MAX_CONCURRENT_ESPLORA_REQUESTS = 2
MAX_PROVIDER_TIP_SKEW = 2
_MAX_RESOLVED_ADDRESSES = 16
_MAX_BLOCK_HEIGHT = 2_147_483_647
_MAINNET_POW_LIMIT = 0xFFFF << (8 * (0x1D - 3))
_MAINNET_GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
_LOWER_BLOCK_HASH = re.compile(r"^[0-9a-f]{64}$")
_UNSIGNED_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class EsploraTransport(Protocol):
    async def get(self, base_url: str, path: str, max_bytes: int) -> bytes: ...


AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


@dataclass(frozen=True, slots=True)
class EsploraConfiguration:
    provider_urls: tuple[str, ...]
    timeout_seconds: float = 5.0
    minimum_confirmations: int = 1

    def validated_urls(self) -> tuple[str, str]:
        if len(self.provider_urls) != 2:
            raise ValueError("exactly_two_esplora_providers_required")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not 0.1 <= self.timeout_seconds <= 30.0
        ):
            raise ValueError("esplora_timeout_out_of_bounds")
        if (
            isinstance(self.minimum_confirmations, bool)
            or not isinstance(self.minimum_confirmations, int)
            or not 1 <= self.minimum_confirmations <= 100
        ):
            raise ValueError("bitcoin_confirmation_threshold_invalid")

        normalized: list[str] = []
        provider_identities: set[tuple[str, str]] = set()
        for raw_url in self.provider_urls:
            if (
                not raw_url
                or raw_url != raw_url.strip()
                or not raw_url.isascii()
                or "\\" in raw_url
                or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw_url)
            ):
                raise ValueError("esplora_provider_url_invalid")
            try:
                parsed = urlsplit(raw_url)
                port = parsed.port
            except ValueError as exc:
                raise ValueError("esplora_provider_url_invalid") from exc
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or port not in {None, 443}
                or (port is None and parsed.netloc.endswith(":"))
            ):
                raise ValueError("esplora_providers_require_https_base_urls")
            base_url = raw_url.rstrip("/") + "/"
            if base_url in normalized:
                raise ValueError("duplicate_esplora_provider")
            normalized.append(base_url)
            provider_identities.add(_canonical_provider_identity(parsed.hostname))
        if len(provider_identities) != 2:
            raise ValueError("independent_esplora_hosts_required")
        return cast(tuple[str, str], tuple(normalized))


def _canonical_provider_identity(hostname: str) -> tuple[str, str]:
    host = hostname.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise ValueError("esplora_provider_hostname_invalid") from None
        try:
            canonical = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("esplora_provider_hostname_invalid") from exc
        labels = canonical.split(".")
        if (
            len(canonical) > 253
            or len(labels) < 2
            or any(not _DNS_LABEL.fullmatch(label) for label in labels)
            or canonical == "localhost"
        ):
            raise ValueError("esplora_provider_hostname_invalid") from None
        return "dns", canonical
    if not _is_globally_routable_unicast(address):
        raise ValueError("esplora_provider_address_not_public")
    return "ip", address.compressed


def _is_globally_routable_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
        and not (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    )


class HttpxEsploraTransport:
    """Bounded HTTP transport with environment proxies and redirects disabled."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._resolver = resolver or _resolve_addresses
        self._resolution_tasks: dict[str, asyncio.Task[tuple[str, ...]]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}

    async def get(self, base_url: str, path: str, max_bytes: int) -> bytes:
        try:
            parsed = urlsplit(base_url)
            if not parsed.hostname:
                raise BitcoinVerifierUnavailable("esplora_provider_url_invalid")
            identity_kind, hostname = _canonical_provider_identity(parsed.hostname)
            address = hostname if identity_kind == "ip" else (await self._pinned_addresses(hostname))[0]
            authority = f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
            target_url = f"https://{authority}{parsed.path}{path}"
            client = self._client_for(hostname)
            async with client.stream(
                "GET",
                target_url,
                headers={"host": parsed.netloc, "accept-encoding": "identity"},
                extensions={"sni_hostname": hostname},
            ) as response:
                if response.status_code != 200:
                    raise BitcoinVerifierUnavailable("esplora_provider_unavailable")
                content_encoding = response.headers.get("content-encoding")
                if content_encoding is not None and content_encoding.strip().lower() != "identity":
                    raise BitcoinVerifierUnavailable("esplora_response_encoding_invalid")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise BitcoinVerifierUnavailable("esplora_response_length_invalid") from exc
                    if declared_length < 1 or declared_length > max_bytes:
                        raise BitcoinVerifierUnavailable("esplora_response_size_invalid")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(chunk) > max_bytes - len(body):
                        raise BitcoinVerifierUnavailable("esplora_response_too_large")
                    body.extend(chunk)
                if not body:
                    raise BitcoinVerifierUnavailable("esplora_response_empty")
                return bytes(body)
        except BitcoinVerifierUnavailable:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise BitcoinVerifierUnavailable("esplora_provider_unavailable") from exc

    async def aclose(self) -> None:
        await asyncio.gather(*(client.aclose() for client in self._clients.values()))

    def _client_for(self, hostname: str) -> httpx.AsyncClient:
        client = self._clients.get(hostname)
        if client is None:
            client = httpx.AsyncClient(
                follow_redirects=False,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                timeout=self._timeout,
                trust_env=False,
                transport=self._transport,
            )
            self._clients[hostname] = client
        return client

    async def _pinned_addresses(self, hostname: str) -> tuple[str, ...]:
        task = self._resolution_tasks.get(hostname)
        if task is None:
            task = asyncio.create_task(self._resolve_public_addresses(hostname))
            self._resolution_tasks[hostname] = task
        return await asyncio.shield(task)

    async def _resolve_public_addresses(self, hostname: str) -> tuple[str, ...]:
        try:
            resolved = tuple(await self._resolver(hostname, 443))
            if not 1 <= len(resolved) <= _MAX_RESOLVED_ADDRESSES:
                raise ValueError
            addresses: list[str] = []
            for raw_address in resolved:
                if not isinstance(raw_address, str) or "%" in raw_address:
                    raise ValueError
                address = ipaddress.ip_address(raw_address)
                if not _is_globally_routable_unicast(address):
                    raise ValueError
                if address.compressed not in addresses:
                    addresses.append(address.compressed)
            if not addresses:
                raise ValueError
            return tuple(addresses)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BitcoinVerifierUnavailable("esplora_provider_address_not_public") from exc


async def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    results = await asyncio.to_thread(
        socket.getaddrinfo,
        hostname,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(cast(str, result[4][0]) for result in results)


@dataclass(frozen=True, slots=True)
class _ProviderSnapshot:
    block_hash: str
    header: bytes
    status_height: int
    in_best_chain: bool
    tip_height: int


@dataclass(frozen=True, slots=True)
class _QuorumSnapshot:
    block_hash: str
    header: bytes
    status_height: int
    in_best_chain: bool
    tip_height: int
    provider_tip_heights: tuple[int, int]


class EsploraBitcoinVerifier:
    def __init__(
        self,
        configuration: EsploraConfiguration,
        transport: EsploraTransport | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._urls = configuration.validated_urls()
        if transport is None:
            owned_transport = HttpxEsploraTransport(configuration.timeout_seconds)
            self._transport: EsploraTransport = owned_transport
            self._owned_transport: HttpxEsploraTransport | None = owned_transport
        else:
            self._transport = transport
            self._owned_transport = None
        self._timeout_seconds = configuration.timeout_seconds
        self._minimum_confirmations = configuration.minimum_confirmations
        self._clock = clock or (lambda: datetime.now(UTC))
        self._request_slots = asyncio.Semaphore(MAX_CONCURRENT_ESPLORA_REQUESTS)

    async def verify_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> BitcoinVerification:
        parsed = deserialize_exact_proof(digest, proof_bytes)
        candidates = sorted(
            {
                (attestation.height, bytes(message))
                for message, attestation in parsed.detached.timestamp.all_attestations()
                if type(attestation) is BitcoinBlockHeaderAttestation
            }
        )
        if not candidates:
            return BitcoinVerification(verified=False, method="esplora-2-of-2-no-attestation")
        if len(candidates) > MAX_BITCOIN_CANDIDATES or any(
            isinstance(height, bool) or height < 0 or height > _MAX_BLOCK_HEIGHT or len(commitment) != 32
            for height, commitment in candidates
        ):
            raise BitcoinEvidenceInvalid("bitcoin_attestation_malformed")

        commitments_by_height: dict[int, set[bytes]] = {}
        for height, commitment in candidates:
            commitments_by_height.setdefault(height, set()).add(commitment)

        pending_snapshots: list[tuple[int, int, _QuorumSnapshot]] = []
        for height in sorted(commitments_by_height):
            snapshot = await self._quorum_snapshot(height)
            if snapshot.status_height != height or not snapshot.in_best_chain or snapshot.tip_height < height:
                raise BitcoinEvidenceInvalid("esplora_noncanonical_block")

            block_hash, merkle_root, block_time = _verify_header(snapshot.header)
            if block_hash != snapshot.block_hash:
                raise BitcoinEvidenceInvalid("esplora_block_header_hash_mismatch")
            if merkle_root not in commitments_by_height[height]:
                continue

            confirmations = snapshot.tip_height - height + 1
            if confirmations < self._minimum_confirmations:
                pending_snapshots.append((height, confirmations, snapshot))
                continue

            await self._require_stable_snapshot(height, snapshot)
            verified_at = self._clock()
            if verified_at.tzinfo is None or verified_at.utcoffset() is None:
                raise BitcoinEvidenceInvalid("bitcoin_verifier_clock_invalid")
            return BitcoinVerification(
                verified=True,
                method="esplora-2-of-2-exact-digest",
                verified_at=verified_at.astimezone(UTC),
                block_height=height,
                block_hash=snapshot.block_hash,
                block_time=block_time,
                confirmation_policy=f"esplora-2-of-2-mainnet-canonical-min-{self._minimum_confirmations}",
                confirmations=confirmations,
            )

        if pending_snapshots:
            for height, _confirmations, snapshot in pending_snapshots:
                await self._require_stable_snapshot(height, snapshot)
            return BitcoinVerification(
                verified=False,
                method="esplora-2-of-2-confirmation-pending",
                confirmations=max(confirmations for _height, confirmations, _snapshot in pending_snapshots),
            )
        raise BitcoinEvidenceInvalid("bitcoin_attestation_merkle_mismatch")

    async def aclose(self) -> None:
        if self._owned_transport is not None:
            await self._owned_transport.aclose()

    async def _quorum_snapshot(self, height: int) -> _QuorumSnapshot:
        hashes = await asyncio.gather(
            *(self._block_hash(url, height) for url in self._urls),
            return_exceptions=True,
        )
        left_hash, right_hash = _require_strings(hashes)
        if left_hash != right_hash:
            raise BitcoinEvidenceInvalid("esplora_provider_block_hash_conflict")
        if height == 0:
            anchors = (left_hash, right_hash)
        else:
            anchor_results = await asyncio.gather(
                *(self._block_hash(url, 0) for url in self._urls),
                return_exceptions=True,
            )
            anchors = _require_strings(anchor_results)
        if anchors[0] != anchors[1]:
            raise BitcoinEvidenceInvalid("esplora_provider_network_conflict")
        if anchors[0] != _MAINNET_GENESIS_HASH:
            raise BitcoinEvidenceInvalid("esplora_wrong_network")
        snapshots = await asyncio.gather(
            *(self._provider_snapshot(url, left_hash) for url in self._urls),
            return_exceptions=True,
        )
        left, right = _require_snapshots(snapshots)
        if (
            left.block_hash != right.block_hash
            or left.header != right.header
            or left.status_height != right.status_height
            or left.in_best_chain != right.in_best_chain
        ):
            raise BitcoinEvidenceInvalid("esplora_provider_evidence_conflict")
        if abs(left.tip_height - right.tip_height) > MAX_PROVIDER_TIP_SKEW:
            raise BitcoinVerifierUnavailable("esplora_provider_tip_skew")
        return _QuorumSnapshot(
            block_hash=left.block_hash,
            header=left.header,
            status_height=left.status_height,
            in_best_chain=left.in_best_chain,
            tip_height=min(left.tip_height, right.tip_height),
            provider_tip_heights=(left.tip_height, right.tip_height),
        )

    async def _require_stable_snapshot(self, height: int, expected: _QuorumSnapshot) -> None:
        try:
            observed = await self._quorum_snapshot(height)
        except BitcoinEvidenceInvalid as exc:
            raise BitcoinVerifierUnavailable("esplora_chain_race") from exc
        if observed != expected:
            raise BitcoinVerifierUnavailable("esplora_chain_race")

    async def _provider_snapshot(self, base_url: str, block_hash: str) -> _ProviderSnapshot:
        header_raw, status_raw, tip_raw = await asyncio.gather(
            self._get(base_url, f"block/{block_hash}/header"),
            self._get(base_url, f"block/{block_hash}/status"),
            self._get(base_url, "blocks/tip/height"),
        )
        try:
            header_hex = _ascii_text(header_raw)
            if len(header_hex) != 160 or header_hex.lower() != header_hex:
                raise ValueError
            header = bytes.fromhex(header_hex)
            status_height, in_best_chain = _parse_status(status_raw)
            tip_height = _parse_height(tip_raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise BitcoinVerifierUnavailable("esplora_provider_response_malformed") from exc
        return _ProviderSnapshot(block_hash, header, status_height, in_best_chain, tip_height)

    async def _block_hash(self, base_url: str, height: int) -> str:
        raw = await self._get(base_url, f"block-height/{height}")
        try:
            block_hash = _ascii_text(raw)
        except UnicodeDecodeError as exc:
            raise BitcoinVerifierUnavailable("esplora_provider_response_malformed") from exc
        if not _LOWER_BLOCK_HASH.fullmatch(block_hash):
            raise BitcoinVerifierUnavailable("esplora_provider_response_malformed")
        return block_hash

    async def _get(self, base_url: str, path: str) -> bytes:
        try:
            async with self._request_slots:
                async with asyncio.timeout(self._timeout_seconds):
                    body = await self._transport.get(base_url, path, ESPLORA_RESPONSE_LIMIT)
        except BitcoinVerifierUnavailable:
            raise
        except Exception as exc:
            raise BitcoinVerifierUnavailable("esplora_provider_unavailable") from exc
        if not body or len(body) > ESPLORA_RESPONSE_LIMIT:
            raise BitcoinVerifierUnavailable("esplora_response_size_invalid")
        return bytes(body)


def _require_snapshots(
    results: list[_ProviderSnapshot | BaseException],
) -> tuple[_ProviderSnapshot, _ProviderSnapshot]:
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            raise BitcoinVerifierUnavailable("esplora_quorum_unavailable") from result
    return cast(tuple[_ProviderSnapshot, _ProviderSnapshot], tuple(results))


def _require_strings(results: list[str | BaseException]) -> tuple[str, str]:
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            raise BitcoinVerifierUnavailable("esplora_quorum_unavailable") from result
    return cast(tuple[str, str], tuple(results))


def _ascii_text(raw: bytes) -> str:
    return raw.decode("ascii").strip()


def _parse_height(raw: bytes) -> int:
    value = _ascii_text(raw)
    if not _UNSIGNED_DECIMAL.fullmatch(value):
        raise ValueError
    height = int(value)
    if height > _MAX_BLOCK_HEIGHT:
        raise ValueError
    return height


def _parse_status(raw: bytes) -> tuple[int, bool]:
    decoded: object = json.loads(_ascii_text(raw))
    if not isinstance(decoded, dict):
        raise ValueError
    value = cast(dict[str, object], decoded)
    height = value.get("height")
    in_best_chain = value.get("in_best_chain")
    if type(height) is not int or not 0 <= height <= _MAX_BLOCK_HEIGHT or type(in_best_chain) is not bool:
        raise ValueError
    return height, in_best_chain


def _verify_header(header: bytes) -> tuple[str, bytes, datetime]:
    if len(header) != 80:
        raise BitcoinEvidenceInvalid("esplora_block_header_malformed")
    digest = hashlib.sha256(hashlib.sha256(header).digest()).digest()
    block_hash = digest[::-1].hex()
    compact = int.from_bytes(header[72:76], "little")
    target = _compact_target(compact)
    if int.from_bytes(digest, "little") > target:
        raise BitcoinEvidenceInvalid("esplora_block_header_pow_invalid")
    try:
        block_time = datetime.fromtimestamp(int.from_bytes(header[68:72], "little"), UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise BitcoinEvidenceInvalid("esplora_block_time_invalid") from exc
    return block_hash, header[36:68], block_time


def _compact_target(compact: int) -> int:
    exponent = compact >> 24
    mantissa = compact & 0x007FFFFF
    negative = compact & 0x00800000
    overflow = mantissa != 0 and (
        exponent > 34 or (mantissa > 0xFF and exponent > 33) or (mantissa > 0xFFFF and exponent > 32)
    )
    if negative or mantissa == 0 or overflow:
        raise BitcoinEvidenceInvalid("esplora_block_header_target_invalid")
    if exponent <= 3:
        target = mantissa >> (8 * (3 - exponent))
    else:
        target = mantissa << (8 * (exponent - 3))
    if target <= 0 or target > _MAINNET_POW_LIMIT:
        raise BitcoinEvidenceInvalid("esplora_block_header_target_invalid")
    return target
