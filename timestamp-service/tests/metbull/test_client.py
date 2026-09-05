from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from app.metbull.client import (
    METBULL_RESPONSE_LIMIT,
    MetbullNotFound,
    MetbullNotOfficial,
    MetbullUnavailable,
    MeteoriticalBulletinClient,
    parse_metbull_record,
)

FIXTURE = Path(__file__).parent / "fixtures/87447.html"
UNKNOWN_FIXTURE = Path(__file__).parent / "fixtures/999999999-unknown.html"


def fixture_html() -> bytes:
    return FIXTURE.read_bytes()


class AsyncBytesStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content


def response(status: int, *, headers: dict[str, str], content: bytes) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=AsyncBytesStream(content))


def test_parser_extracts_only_bounded_official_entry_fields_from_malformed_html() -> None:
    record = parse_metbull_record(fixture_html(), 87447)
    assert record.code == 87447
    assert record.official_url == "https://www.lpi.usra.edu/meteor/metbull.cfm?code=87447"
    assert record.canonical_name == "Northwest Africa 18652"
    assert record.record_status == "Relict"
    assert record.official_name is True
    assert record.recommended_classification == "Relict iron"
    assert record.fall_or_find == "Find"
    assert record.year_found == 2018
    assert record.country == "Western Sahara"
    assert record.latitude is None
    assert record.longitude is None
    assert not hasattr(record, "mass")
    assert not hasattr(record, "finder")
    assert not hasattr(record, "provenance")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda html: html.replace(b"<main class=\"container\"", b"<main class=\"content\""),
        lambda html: html.replace(b"?code=87447", b"?code=87448"),
        lambda html: html.replace(
            b"<strong>Recommended:</strong>",
            b"<strong>Recommended:</strong>X<br><strong>Recommended:</strong>",
        ),
        lambda html: html.replace(
            b"<strong>Status: </strong>Relict",
            b"<strong>Status: </strong>Relict<br><strong>Status:</strong>Official",
        ),
        lambda html: html.replace(b"Relict iron", b"Iron, ungrouped", 1),
        lambda html: html.replace(b"<strong>Coordinates:</strong> Unknown.", b"<strong>Coordinates:</strong> 91.0, 0"),
    ],
)
def test_parser_rejects_missing_duplicate_ambiguous_or_incoherent_fields(
    mutate: Callable[[bytes], bytes],
) -> None:
    changed = mutate(fixture_html())
    with pytest.raises(MetbullUnavailable):
        parse_metbull_record(changed, 87447)


def test_parser_distinguishes_explicit_nonofficial_records() -> None:
    unofficial = fixture_html().replace(b"This is an official name", b"This is an unofficial name")
    with pytest.raises(MetbullNotOfficial):
        parse_metbull_record(unofficial, 87447)


def test_parser_recognizes_exact_official_unknown_entry_structure() -> None:
    with pytest.raises(MetbullNotFound):
        parse_metbull_record(UNKNOWN_FIXTURE.read_bytes(), 999999999)


@pytest.mark.parametrize(
    "unknown",
    [
        b'<main class="container"><p>No records found for that code.</p></main>',
        UNKNOWN_FIXTURE.read_bytes().replace(b"<title>", b"<title>changed "),
        UNKNOWN_FIXTURE.read_bytes().replace(b"noindex,nofollow", b"index,nofollow"),
        UNKNOWN_FIXTURE.read_bytes().replace(b"<main class=\"container\" role=\"main\">", b'<main class="container">'),
        UNKNOWN_FIXTURE.read_bytes().replace(b"<style>", b"<h1>unexpected</h1><style>"),
    ],
)
def test_parser_does_not_treat_missing_or_drifted_entry_content_as_not_found(unknown: bytes) -> None:
    with pytest.raises(MetbullUnavailable):
        parse_metbull_record(unknown, 999999999)


@pytest.mark.asyncio
async def test_client_maps_official_unknown_response_to_not_found() -> None:
    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)

    transport = httpx.MockTransport(
        lambda _request: response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=UNKNOWN_FIXTURE.read_bytes(),
        )
    )
    client = MeteoriticalBulletinClient(resolver=resolver, transport=transport)
    with pytest.raises(MetbullNotFound):
        await client.lookup(999999999)


def test_parser_accepts_strict_decimal_coordinates() -> None:
    html = fixture_html().replace(b"Unknown.", b"27.125, -13.500")
    record = parse_metbull_record(html, 87447)
    assert (record.latitude, record.longitude) == ("27.125", "-13.500")


@pytest.mark.asyncio
async def test_client_pins_public_ip_and_preserves_fixed_host_path_and_sni() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(200, headers={"content-type": "text/html; charset=utf-8"}, content=fixture_html())

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        assert (hostname, port) == ("www.lpi.usra.edu", 443)
        return ("8.8.8.8",)

    client = MeteoriticalBulletinClient(resolver=resolver, transport=httpx.MockTransport(handler))
    record = await client.lookup(87447)
    assert record.canonical_name == "Northwest Africa 18652"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://8.8.8.8/meteor/metbull.cfm?code=87447"
    assert request.headers["host"] == "www.lpi.usra.edu"
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["sni_hostname"] == "www.lpi.usra.edu"


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [(), ("127.0.0.1",), ("10.0.0.1", "8.8.8.8"), ("224.0.0.1",), ("fe80::1",)])
async def test_client_rejects_empty_or_any_nonpublic_dns_snapshot(addresses: tuple[str, ...]) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(200, headers={"content-type": "text/html"}, content=fixture_html())

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return addresses

    client = MeteoriticalBulletinClient(resolver=resolver, transport=httpx.MockTransport(handler))
    with pytest.raises(MetbullUnavailable):
        await client.lookup(87447)
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "body", "exception"),
    [
        (404, {"content-type": "text/html"}, b"missing", MetbullNotFound),
        (302, {"content-type": "text/html", "location": "https://evil.example/"}, b"redirect", MetbullUnavailable),
        (200, {"content-type": "application/json"}, b"{}", MetbullUnavailable),
        (200, {"content-type": "text/html", "content-encoding": "gzip"}, b"bad", MetbullUnavailable),
        (
            200,
            {"content-type": "text/html", "content-length": str(METBULL_RESPONSE_LIMIT + 1)},
            b"bad",
            MetbullUnavailable,
        ),
    ],
)
async def test_client_rejects_bad_status_type_encoding_and_declared_size(
    status: int,
    headers: dict[str, str],
    body: bytes,
    exception: type[Exception],
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(status, headers=headers, content=body)

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)

    client = MeteoriticalBulletinClient(resolver=resolver, transport=httpx.MockTransport(handler))
    with pytest.raises(exception):
        await client.lookup(87447)
    assert calls == 1


@pytest.mark.asyncio
async def test_client_enforces_raw_stream_and_aggregate_timeout_bounds() -> None:
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"x" * (METBULL_RESPONSE_LIMIT + 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=OversizedStream())

    async def public_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)

    oversized = MeteoriticalBulletinClient(resolver=public_resolver, transport=httpx.MockTransport(handler))
    with pytest.raises(MetbullUnavailable):
        await oversized.lookup(87447)

    async def slow_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        await asyncio.sleep(0.2)
        return ("8.8.8.8",)

    timed = MeteoriticalBulletinClient(0.1, resolver=slow_resolver, transport=httpx.MockTransport(handler))
    with pytest.raises(MetbullUnavailable):
        await timed.lookup(87447)


@pytest.mark.parametrize("code", [True, "87447", 0, -1, 1_000_000_000])
@pytest.mark.asyncio
async def test_client_rejects_noncanonical_numeric_codes_without_dns(code: object) -> None:
    called = False

    async def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        nonlocal called
        called = True
        return ("8.8.8.8",)

    with pytest.raises(ValueError):
        await MeteoriticalBulletinClient(resolver=resolver).lookup(code)  # type: ignore[arg-type]
    assert called is False
