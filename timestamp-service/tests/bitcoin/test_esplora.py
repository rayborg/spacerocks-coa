from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation

from app.bitcoin.esplora import (
    ESPLORA_RESPONSE_LIMIT,
    EsploraBitcoinVerifier,
    EsploraConfiguration,
    HttpxEsploraTransport,
)
from app.domain.digest import ManifestDigest
from app.ports.bitcoin import BitcoinEvidenceInvalid, BitcoinVerifierUnavailable
from app.timestamping.detached import ProofValidationError, new_detached_exact_digest, serialize_detached

GENESIS_HEADER = bytes.fromhex(
    "01000000"
    + "00" * 32
    + "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
    + "29ab5f49"
    + "ffff001d"
    + "1dac2b7c"
)
GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
TESTNET_GENESIS_HEADER = bytes.fromhex(
    "01000000"
    + "00" * 32
    + "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
    + "dae5494d"
    + "ffff001d"
    + "1aa4ae18"
)
TESTNET_GENESIS_HASH = hashlib.sha256(hashlib.sha256(TESTNET_GENESIS_HEADER).digest()).digest()[::-1].hex()
MERKLE_ROOT = GENESIS_HEADER[36:68]
URLS = ("https://esplora-a.example/api/", "https://esplora-b.example/api/")
OBSERVED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class AsyncBytesStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content


class FakeTransport:
    def __init__(
        self,
        *,
        tip: int = 0,
        mutate: Callable[[str, str, bytes], bytes] | None = None,
        delay: float = 0,
    ) -> None:
        self.tip = tip
        self.mutate = mutate
        self.delay = delay
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.maximum_active = 0

    async def get(self, base_url: str, path: str, max_bytes: int) -> bytes:
        assert max_bytes == ESPLORA_RESPONSE_LIMIT
        self.calls.append((base_url, path))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if path == "block-height/0":
                result = GENESIS_HASH.encode()
            elif path.startswith("block/") and path.endswith("/header"):
                result = GENESIS_HEADER.hex().encode()
            elif path.startswith("block/") and path.endswith("/status"):
                result = b'{"in_best_chain":true,"height":0,"next_best":"ignored-compatible-field"}'
            elif path == "blocks/tip/height":
                result = str(self.tip).encode()
            else:
                raise AssertionError(f"unexpected request: {path}")
            return self.mutate(base_url, path, result) if self.mutate else result
        finally:
            self.active -= 1


def _proof(digest: ManifestDigest, *, heights: tuple[int, ...] = (0,)) -> bytes:
    detached = new_detached_exact_digest(digest)
    for height in heights:
        detached.timestamp.attestations.add(BitcoinBlockHeaderAttestation(height))
    return serialize_detached(detached)


def _verifier(
    transport: FakeTransport,
    *,
    minimum_confirmations: int = 1,
    timeout_seconds: float = 5.0,
) -> EsploraBitcoinVerifier:
    return EsploraBitcoinVerifier(
        EsploraConfiguration(
            URLS,
            timeout_seconds=timeout_seconds,
            minimum_confirmations=minimum_confirmations,
        ),
        transport,
        clock=lambda: OBSERVED_AT,
    )


@pytest.mark.asyncio
async def test_default_one_confirmation_requires_two_providers_and_returns_complete_metadata() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    transport = FakeTransport(tip=0)

    result = await _verifier(transport).verify_exact_digest(digest, _proof(digest))

    assert result.verified
    assert result.method == "esplora-2-of-2-exact-digest"
    assert result.verified_at == OBSERVED_AT
    assert result.block_height == 0
    assert result.block_hash == GENESIS_HASH
    assert result.block_time == datetime(2009, 1, 3, 18, 15, 5, tzinfo=UTC)
    assert result.confirmations == 1
    assert result.confirmation_policy == "esplora-2-of-2-mainnet-canonical-min-1"
    assert len(transport.calls) == 16
    assert {base_url for base_url, _path in transport.calls} == set(URLS)
    assert all(digest.hex not in path and _proof(digest).hex() not in path for _base_url, path in transport.calls)


@pytest.mark.asyncio
async def test_one_through_threshold_minus_one_are_pending_and_threshold_verifies() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    for confirmations in range(1, 6):
        pending = await _verifier(
            FakeTransport(tip=confirmations - 1),
            minimum_confirmations=6,
        ).verify_exact_digest(digest, _proof(digest))
        assert not pending.verified
        assert pending.method == "esplora-2-of-2-confirmation-pending"
        assert pending.confirmations == confirmations

    verified = await _verifier(FakeTransport(tip=5), minimum_confirmations=6).verify_exact_digest(
        digest,
        _proof(digest),
    )
    assert verified.verified
    assert verified.confirmations == 6
    assert verified.confirmation_policy == "esplora-2-of-2-mainnet-canonical-min-6"


@pytest.mark.asyncio
async def test_no_bitcoin_attestation_is_pending_without_provider_requests() -> None:
    digest = ManifestDigest.from_hex("10" * 32)
    detached = new_detached_exact_digest(digest)
    detached.timestamp.attestations.add(PendingAttestation("https://calendar.example/"))
    transport = FakeTransport()

    result = await _verifier(transport).verify_exact_digest(digest, serialize_detached(detached))

    assert not result.verified
    assert result.method == "esplora-2-of-2-no-attestation"
    assert result.confirmations is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_wrong_exact_target_is_rejected_before_any_provider_request() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    transport = FakeTransport()

    with pytest.raises(ProofValidationError, match="proof_target_mismatch"):
        await _verifier(transport).verify_exact_digest(ManifestDigest.from_hex("ab" * 32), _proof(digest))

    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hash", "header", "status"])
async def test_provider_conflict_never_verifies(kind: str) -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)

    def disagree(base_url: str, path: str, result: bytes) -> bytes:
        if base_url != URLS[1]:
            return result
        if kind == "hash" and path == "block-height/0":
            return ("01" * 32).encode()
        if kind == "header" and path.endswith("/header"):
            return (GENESIS_HEADER[:-1] + bytes([GENESIS_HEADER[-1] ^ 1])).hex().encode()
        if kind == "status" and path.endswith("/status"):
            return b'{"in_best_chain":false,"height":0}'
        return result

    with pytest.raises(BitcoinEvidenceInvalid, match="conflict"):
        await _verifier(FakeTransport(mutate=disagree)).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_provider_unavailability_malformed_and_oversized_responses_fail_closed() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)

    class Outage(FakeTransport):
        async def get(self, base_url: str, path: str, max_bytes: int) -> bytes:
            if base_url == URLS[1]:
                raise OSError("provider offline")
            return await super().get(base_url, path, max_bytes)

    with pytest.raises(BitcoinVerifierUnavailable, match="quorum"):
        await _verifier(Outage()).verify_exact_digest(digest, _proof(digest))

    malformed_values = {
        "block-height/0": b"not-a-hash",
        f"block/{GENESIS_HASH}/header": b"xyz",
        f"block/{GENESIS_HASH}/status": b'{"height":true,"in_best_chain":true}',
        "blocks/tip/height": b"+1",
    }
    for target_path, malformed in malformed_values.items():

        def mutate(
            base_url: str,
            path: str,
            result: bytes,
            *,
            target: str = target_path,
            value: bytes = malformed,
        ) -> bytes:
            return value if base_url == URLS[1] and path == target else result

        with pytest.raises(BitcoinVerifierUnavailable):
            await _verifier(FakeTransport(mutate=mutate)).verify_exact_digest(digest, _proof(digest))

    def oversized(base_url: str, path: str, result: bytes) -> bytes:
        if base_url == URLS[1] and path == "blocks/tip/height":
            return b"x" * (ESPLORA_RESPONSE_LIMIT + 1)
        return result

    with pytest.raises(BitcoinVerifierUnavailable, match="quorum"):
        await _verifier(FakeTransport(mutate=oversized)).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_tip_skew_uses_lower_tip_within_bound_and_rejects_larger_skew() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)

    def within_bound(base_url: str, path: str, result: bytes) -> bytes:
        return b"6" if base_url == URLS[1] and path == "blocks/tip/height" else result

    pending = await _verifier(
        FakeTransport(tip=4, mutate=within_bound),
        minimum_confirmations=6,
    ).verify_exact_digest(digest, _proof(digest))
    assert not pending.verified
    assert pending.confirmations == 5

    def outside_bound(base_url: str, path: str, result: bytes) -> bytes:
        return b"7" if base_url == URLS[1] and path == "blocks/tip/height" else result

    with pytest.raises(BitcoinVerifierUnavailable, match="tip_skew"):
        await _verifier(FakeTransport(tip=4, mutate=outside_bound)).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_wrong_merkle_header_hash_pow_and_noncanonical_status_fail_closed() -> None:
    wrong_digest = ManifestDigest.from_hex("ab" * 32)
    with pytest.raises(BitcoinEvidenceInvalid, match="merkle"):
        await _verifier(FakeTransport()).verify_exact_digest(wrong_digest, _proof(wrong_digest))

    digest = ManifestDigest.from_bytes(MERKLE_ROOT)

    def wrong_hash(_base_url: str, path: str, result: bytes) -> bytes:
        return ("01" * 32).encode() if path == "block-height/0" else result

    with pytest.raises(BitcoinEvidenceInvalid, match="wrong_network"):
        await _verifier(FakeTransport(mutate=wrong_hash)).verify_exact_digest(digest, _proof(digest))

    invalid_pow_header = bytearray(GENESIS_HEADER)
    invalid_pow_header[-4:] = b"\x00\x00\x00\x00"
    invalid_pow_header_bytes = bytes(invalid_pow_header)

    def invalid_pow(_base_url: str, path: str, result: bytes) -> bytes:
        if path.endswith("/header"):
            return invalid_pow_header_bytes.hex().encode()
        return result

    with pytest.raises(BitcoinEvidenceInvalid, match="pow_invalid"):
        await _verifier(FakeTransport(mutate=invalid_pow)).verify_exact_digest(digest, _proof(digest))

    def noncanonical(_base_url: str, path: str, result: bytes) -> bytes:
        return b'{"in_best_chain":false,"height":0}' if path.endswith("/status") else result

    with pytest.raises(BitcoinEvidenceInvalid, match="noncanonical"):
        await _verifier(FakeTransport(mutate=noncanonical)).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_reorg_reread_fails_closed_for_pending_and_verified_results() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    for minimum_confirmations in (1, 6):
        reads = 0

        def reorg(_base_url: str, path: str, result: bytes) -> bytes:
            nonlocal reads
            if path == "block-height/0":
                reads += 1
                if reads > 2:
                    return ("01" * 32).encode()
            return result

        with pytest.raises(BitcoinVerifierUnavailable, match="chain_race"):
            await _verifier(
                FakeTransport(tip=0, mutate=reorg),
                minimum_confirmations=minimum_confirmations,
            ).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tip", "canonical"])
@pytest.mark.parametrize("minimum_confirmations", [1, 6])
async def test_tip_and_canonical_races_fail_closed_for_pending_and_verified_results(
    kind: str,
    minimum_confirmations: int,
) -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    reads = 0

    def race(base_url: str, path: str, result: bytes) -> bytes:
        nonlocal reads
        target = path == "blocks/tip/height" if kind == "tip" else path.endswith("/status")
        if target and base_url == URLS[1]:
            reads += 1
            if reads > 1:
                return b"1" if kind == "tip" else b'{"in_best_chain":false,"height":0}'
        return result

    with pytest.raises(BitcoinVerifierUnavailable, match="chain_race"):
        await _verifier(
            FakeTransport(tip=0, mutate=race),
            minimum_confirmations=minimum_confirmations,
        ).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_testnet_genesis_and_header_cannot_satisfy_mainnet_quorum() -> None:
    digest = ManifestDigest.from_bytes(TESTNET_GENESIS_HEADER[36:68])

    def testnet(_base_url: str, path: str, result: bytes) -> bytes:
        if path == "block-height/0":
            return TESTNET_GENESIS_HASH.encode()
        if path.endswith("/header"):
            return TESTNET_GENESIS_HEADER.hex().encode()
        return result

    with pytest.raises(BitcoinEvidenceInvalid, match="wrong_network"):
        await _verifier(FakeTransport(mutate=testnet)).verify_exact_digest(digest, _proof(digest))


@pytest.mark.asyncio
async def test_request_concurrency_timeout_and_candidate_count_are_bounded() -> None:
    digest = ManifestDigest.from_bytes(MERKLE_ROOT)
    transport = FakeTransport(delay=0.001)
    assert (await _verifier(transport).verify_exact_digest(digest, _proof(digest))).verified
    assert transport.maximum_active == 2

    with pytest.raises(BitcoinVerifierUnavailable, match="quorum"):
        await _verifier(FakeTransport(delay=0.2), timeout_seconds=0.1).verify_exact_digest(digest, _proof(digest))

    too_many = _proof(digest, heights=tuple(range(17)))
    untouched = FakeTransport()
    with pytest.raises(BitcoinEvidenceInvalid, match="attestation_malformed"):
        await _verifier(untouched).verify_exact_digest(digest, too_many)
    assert untouched.calls == []


def test_configuration_requires_exactly_two_independent_public_https_bases() -> None:
    invalid_pairs = [
        ("https://only-one.example/",),
        (URLS[0], URLS[0]),
        ("https://same.example/a", "https://same.example/b"),
        ("http://esplora-a.example/", URLS[1]),
        ("https://user:secret@esplora-a.example/", URLS[1]),
        ("https://esplora-a.example/?query=1", URLS[1]),
        ("https://127.0.0.1/", URLS[1]),
        ("https://127.1/", URLS[1]),
        ("https://2130706433/", URLS[1]),
        ("https://10.0.0.1/", URLS[1]),
        ("https://224.0.0.251/", URLS[1]),
        ("https://[::1]/", URLS[1]),
        ("https://[ff02::1]/", URLS[1]),
        ("https://[fec0::1]/", URLS[1]),
        ("https://localhost/", URLS[1]),
    ]
    for providers in invalid_pairs:
        with pytest.raises(ValueError):
            EsploraConfiguration(providers).validated_urls()

    with pytest.raises(ValueError, match="threshold"):
        EsploraConfiguration(URLS, minimum_confirmations=0).validated_urls()
    with pytest.raises(ValueError, match="timeout"):
        EsploraConfiguration(URLS, timeout_seconds=31).validated_urls()

    assert EsploraConfiguration(URLS).validated_urls() == URLS


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://provider.example/api/", "https://provider.example./api/"),
        ("https://[2606:4700:4700::1111]/api/", "https://[2606:4700:4700:0:0:0:0:1111]/api/"),
    ],
)
def test_equivalent_provider_host_aliases_cannot_fill_both_quorum_positions(first: str, second: str) -> None:
    with pytest.raises(ValueError, match="independent"):
        EsploraConfiguration((first, second)).validated_urls()


@pytest.mark.asyncio
async def test_http_transport_rejects_redirects_and_oversized_streams() -> None:
    redirects = 0

    def redirect(request: httpx.Request) -> httpx.Response:
        nonlocal redirects
        redirects += 1
        return httpx.Response(302, headers={"location": "https://redirected.example/secret"})

    async def public_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    transport = HttpxEsploraTransport(1, transport=httpx.MockTransport(redirect), resolver=public_resolver)
    with pytest.raises(BitcoinVerifierUnavailable, match="unavailable"):
        await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()
    assert redirects == 1

    oversized = HttpxEsploraTransport(
        1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=AsyncBytesStream(b"x" * (ESPLORA_RESPONSE_LIMIT + 1)),
                request=request,
            )
        ),
        resolver=public_resolver,
    )
    with pytest.raises(BitcoinVerifierUnavailable, match="too_large|size"):
        await oversized.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await oversized.aclose()


@pytest.mark.asyncio
async def test_http_transport_pins_public_dns_while_preserving_tls_and_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    requests: list[httpx.Request] = []

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        assert (hostname, port) == ("esplora-a.example", 443)
        return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-encoding": "identity"},
            stream=AsyncBytesStream(b"1"),
            request=request,
        )

    original_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        captured.update(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    transport = HttpxEsploraTransport(1, transport=httpx.MockTransport(response), resolver=resolver)
    await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert requests[0].url == "https://93.184.216.34/api/blocks/tip/height"
    assert requests[0].headers["host"] == "esplora-a.example"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].extensions["sni_hostname"] == "esplora-a.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("224.0.0.251",),
        ("::1",),
        ("ff02::1",),
        ("fec0::1",),
        ("93.184.216.34", "10.0.0.1"),
    ],
)
async def test_http_transport_rejects_private_or_mixed_dns_results(addresses: tuple[str, ...]) -> None:
    requests = 0

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return addresses

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=b"1", request=request)

    transport = HttpxEsploraTransport(1, transport=httpx.MockTransport(response), resolver=resolver)
    with pytest.raises(BitcoinVerifierUnavailable, match="not_public"):
        await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()
    assert requests == 0


@pytest.mark.asyncio
async def test_http_transport_resolves_once_and_cannot_rebind_after_validation() -> None:
    resolutions = 0
    targets: list[str] = []

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        nonlocal resolutions
        resolutions += 1
        return ("93.184.216.34",) if resolutions == 1 else ("127.0.0.1",)

    def response(request: httpx.Request) -> httpx.Response:
        targets.append(str(request.url))
        return httpx.Response(200, stream=AsyncBytesStream(b"1"), request=request)

    transport = HttpxEsploraTransport(1, transport=httpx.MockTransport(response), resolver=resolver)
    await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()

    assert resolutions == 1
    assert targets == [
        "https://93.184.216.34/api/blocks/tip/height",
        "https://93.184.216.34/api/blocks/tip/height",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("content_encoding", ["gzip", "br", "identity, gzip"])
async def test_http_transport_rejects_non_identity_content_encoding(content_encoding: str) -> None:
    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    transport = HttpxEsploraTransport(
        1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-encoding": content_encoding},
                content=b"1",
                request=request,
            )
        ),
        resolver=resolver,
    )
    with pytest.raises(BitcoinVerifierUnavailable, match="encoding_invalid"):
        await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()


@pytest.mark.asyncio
async def test_http_transport_rejects_compressed_bomb_without_consuming_or_decoding_body() -> None:
    compressed_bomb = gzip.compress(b"x" * (ESPLORA_RESPONSE_LIMIT * 100))
    assert len(compressed_bomb) < ESPLORA_RESPONSE_LIMIT

    class BombStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.iterated = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.iterated = True
            yield compressed_bomb

    stream = BombStream()

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    transport = HttpxEsploraTransport(
        1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=stream,
                request=request,
            )
        ),
        resolver=resolver,
    )
    with pytest.raises(BitcoinVerifierUnavailable, match="encoding_invalid"):
        await transport.get(URLS[0], "blocks/tip/height", ESPLORA_RESPONSE_LIMIT)
    await transport.aclose()

    assert not stream.iterated
