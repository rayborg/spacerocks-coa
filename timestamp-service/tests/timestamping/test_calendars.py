from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

import httpx
import pytest
from opentimestamps.core.notary import PendingAttestation
from opentimestamps.core.op import OpAppend
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import Timestamp

from app.domain.digest import ManifestDigest
from app.timestamping import calendars as calendar_module
from app.timestamping.calendars import (
    MAX_CONCURRENT_CALENDAR_REQUESTS,
    CalendarConfiguration,
    CalendarUnavailable,
    HardenedCalendarTransport,
    MultiCalendarTimestamper,
)
from app.timestamping.detached import deserialize_exact_proof, new_detached_exact_digest, serialize_detached

TLS_FIXTURES = Path(__file__).with_name("fixtures")
TLS_CERTIFICATE = TLS_FIXTURES / "calendar-a-cert.pem"
TLS_PRIVATE_KEY = TLS_FIXTURES / "calendar-a-key.pem"


def _response(message: bytes, uri: str) -> bytes:
    timestamp = Timestamp(message)
    timestamp.attestations.add(PendingAttestation(uri))
    context = BytesSerializationContext()
    timestamp.serialize(context)
    return context.getbytes()


class FakeTransport:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.calls: list[tuple[str, bytes]] = []

    async def submit(self, base_url: str, digest: bytes) -> bytes:
        self.calls.append((base_url, digest))
        if base_url in self.failures:
            raise CalendarUnavailable("fixture_outage")
        return _response(digest, base_url)

    async def upgrade(self, base_url: str, commitment: bytes) -> bytes | None:
        self.calls.append((base_url, commitment))
        return None


def _config() -> CalendarConfiguration:
    return CalendarConfiguration(
        allowlist=("https://calendar-a.example/", "https://calendar-b.example/"),
        enabled=True,
    )


@pytest.mark.asyncio
async def test_partial_calendar_outage_preserves_success_and_exact_target() -> None:
    digest = ManifestDigest.from_hex("12" * 32)
    transport = FakeTransport({"https://calendar-b.example/"})
    proof = await MultiCalendarTimestamper(_config(), transport).stamp_exact_digest(digest)
    parsed = deserialize_exact_proof(digest, proof.proof_bytes)
    assert parsed.detached.file_digest == digest.value
    assert len(transport.calls) == 2
    assert all(call_digest == digest.value for _, call_digest in transport.calls)


@pytest.mark.asyncio
async def test_total_calendar_outage_remains_unavailable_not_confirmed() -> None:
    urls = {"https://calendar-a.example/", "https://calendar-b.example/"}
    with pytest.raises(CalendarUnavailable, match="all_calendars_unavailable"):
        await MultiCalendarTimestamper(_config(), FakeTransport(urls)).stamp_exact_digest(
            ManifestDigest.from_hex("34" * 32)
        )


def test_real_network_is_off_by_default_and_allowlist_is_operator_only() -> None:
    with pytest.raises(ValueError, match="real_calendars_disabled"):
        MultiCalendarTimestamper(CalendarConfiguration())
    with pytest.raises(ValueError, match="operator_https"):
        MultiCalendarTimestamper(
            CalendarConfiguration(allowlist=("http://127.0.0.1/", "https://calendar.example/"), enabled=True)
        )
    MultiCalendarTimestamper(_config())
    too_many = tuple(f"https://calendar-{index}.example/" for index in range(9))
    with pytest.raises(ValueError, match="calendar_allowlist_too_large"):
        MultiCalendarTimestamper(CalendarConfiguration(allowlist=too_many, enabled=True), FakeTransport(set()))


class ConcurrencyTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__(set())
        self.active = 0
        self.maximum_active = 0

    async def submit(self, base_url: str, digest: bytes) -> bytes:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return await super().submit(base_url, digest)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_calendar_concurrency_and_upgrade_fanout_are_bounded_before_requests() -> None:
    urls = tuple(f"https://calendar-{index}.example/" for index in range(8))
    config = CalendarConfiguration(allowlist=urls, enabled=True)
    transport = ConcurrencyTransport()
    digest = ManifestDigest.from_hex("35" * 32)
    await MultiCalendarTimestamper(config, transport).stamp_exact_digest(digest)
    assert transport.maximum_active == MAX_CONCURRENT_CALENDAR_REQUESTS

    detached = new_detached_exact_digest(digest)
    for index in range(9):
        node = detached.timestamp.ops.add(OpAppend(index.to_bytes(2)))
        node.attestations.add(PendingAttestation(urls[index % len(urls)]))
    proof_bytes = serialize_detached(detached)
    upgrade_transport = FakeTransport(set())
    with pytest.raises(CalendarUnavailable, match="calendar_upgrade_fanout_exceeded"):
        await MultiCalendarTimestamper(config, upgrade_transport).upgrade_exact_digest(digest, proof_bytes)
    assert upgrade_transport.calls == []


@pytest.mark.parametrize(
    "invalid_url",
    (
        "https://user:password@calendar-a.example/",
        "https://127.0.0.1/",
        "https://[2001:db8::1]/",
        "https://calendar-a.example/?redirect=https://private.invalid/",
        "https://calendar-a.example/#fragment",
        "https://calendar-a.example\\@private.invalid/",
    ),
)
def test_calendar_url_validation_rejects_ambiguous_or_non_hostname_targets(invalid_url: str) -> None:
    config = CalendarConfiguration(allowlist=(invalid_url, "https://calendar-b.example/"), enabled=True)
    with pytest.raises(ValueError, match="operator_https"):
        config.validated_urls()


@pytest.mark.asyncio
async def test_hardened_transport_pins_public_ip_but_preserves_host_and_sni() -> None:
    observed: list[httpx.Request] = []

    async def resolve(hostname: str, port: int) -> tuple[str, ...]:
        assert (hostname, port) == ("calendar-a.example", 443)
        return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    def respond(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, content=b"calendar-response")

    transport = HardenedCalendarTransport(
        1.0,
        resolver=resolve,
        transport=httpx.MockTransport(respond),
    )
    result = await transport.submit("https://calendar-a.example/ots/", b"a" * 32)
    assert result == b"calendar-response"
    assert len(observed) == 1
    assert observed[0].url == httpx.URL("https://93.184.216.34/ots/digest")
    assert observed[0].headers["host"] == "calendar-a.example"
    assert observed[0].extensions["sni_hostname"] == "calendar-a.example"
    assert observed[0].content == b"a" * 32


@pytest.mark.asyncio
async def test_hardened_transport_rejects_mixed_or_private_dns_before_request() -> None:
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=b"unexpected")

    for addresses in (("93.184.216.34", "10.0.0.2"), ("127.0.0.1",)):
        async def resolve(hostname: str, port: int, snapshot: tuple[str, ...] = addresses) -> tuple[str, ...]:
            del hostname, port
            return snapshot

        transport = HardenedCalendarTransport(1.0, resolver=resolve, transport=httpx.MockTransport(respond))
        with pytest.raises(CalendarUnavailable, match="dns_address_not_public"):
            await transport.submit("https://calendar-a.example/", b"b" * 32)
    assert requests == 0


@pytest.mark.asyncio
async def test_hardened_transport_rejects_redirects_and_oversized_responses() -> None:
    async def resolve(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("93.184.216.34",)

    redirect = HardenedCalendarTransport(
        1.0,
        resolver=resolve,
        transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/"})),
    )
    with pytest.raises(CalendarUnavailable, match="http_response_invalid"):
        await redirect.submit("https://calendar-a.example/", b"c" * 32)

    oversized = HardenedCalendarTransport(
        1.0,
        resolver=resolve,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 10_001)),
    )
    with pytest.raises(CalendarUnavailable, match="response_invalid"):
        await oversized.upgrade("https://calendar-a.example/", b"d" * 32)

    not_ready = HardenedCalendarTransport(
        1.0,
        resolver=resolve,
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    assert await not_ready.upgrade("https://calendar-a.example/", b"d" * 32) is None


@pytest.mark.asyncio
async def test_hardened_transport_applies_timeout_to_dns_resolution() -> None:
    async def stalled_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        await asyncio.sleep(1)
        return ("93.184.216.34",)

    transport = HardenedCalendarTransport(
        0.1,
        resolver=stalled_resolver,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"unexpected")),
    )
    with pytest.raises(CalendarUnavailable, match="request_unavailable"):
        await transport.submit("https://calendar-a.example/", b"e" * 32)


async def _start_tls_calendar() -> tuple[asyncio.Server, list[str | None], list[bytes], list[str]]:
    observed_sni: list[str | None] = []
    observed_requests: list[bytes] = []
    observed_peers: list[str] = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(TLS_CERTIFICATE, TLS_PRIVATE_KEY)

    def capture_sni(ssl_socket: object, server_name: str | None, ssl_context: ssl.SSLContext) -> None:
        del ssl_socket, ssl_context
        observed_sni.append(server_name)

    context.set_servername_callback(capture_sni)

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            content_length = next(
                int(line.split(b":", 1)[1].strip())
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            body = await reader.readexactly(content_length)
            observed_requests.append(headers + body)
            peer = writer.get_extra_info("peername")
            if isinstance(peer, tuple) and peer:
                observed_peers.append(str(peer[0]))
            response = b"calendar-response"
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(response)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + response
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0, ssl=context)
    return server, observed_sni, observed_requests, observed_peers


def _tls_client_context(*, trust_fixture: bool) -> ssl.SSLContext:
    if trust_fixture:
        return ssl.create_default_context(cafile=TLS_CERTIFICATE)
    return ssl.create_default_context()


class LocalTlsTransport(httpx.AsyncBaseTransport):
    def __init__(self, port: int, context: ssl.SSLContext) -> None:
        self._port = port
        self._transport = httpx.AsyncHTTPTransport(verify=context, retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(port=self._port)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


@pytest.mark.asyncio
async def test_real_tls_uses_pinned_ip_with_original_host_sni_and_certificate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, observed_sni, observed_requests, observed_peers = await _start_tls_calendar()
    assert server.sockets
    port = int(server.sockets[0].getsockname()[1])
    resolver_calls: list[tuple[str, int]] = []

    async def resolve(hostname: str, requested_port: int) -> tuple[str, ...]:
        resolver_calls.append((hostname, requested_port))
        return ("127.0.0.1",)

    # Other tests exercise the production private-address rejection; this test needs a local TLS peer.
    monkeypatch.setattr(calendar_module, "_vetted_public_addresses", lambda addresses: addresses)
    transport = HardenedCalendarTransport(
        2.0,
        resolver=resolve,
        transport=LocalTlsTransport(port, _tls_client_context(trust_fixture=True)),
    )
    try:
        response = await transport.submit("https://calendar-a.example/", b"f" * 32)
    finally:
        server.close()
        await server.wait_closed()

    assert response == b"calendar-response"
    assert resolver_calls == [("calendar-a.example", 443)]
    assert observed_sni == ["calendar-a.example"]
    assert observed_peers == ["127.0.0.1"]
    assert len(observed_requests) == 1
    assert b"Host: calendar-a.example\r\n" in observed_requests[0]
    assert observed_requests[0].endswith(b"f" * 32)


@pytest.mark.asyncio
async def test_real_tls_rejects_mismatched_hostname_and_untrusted_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calendar_module, "_vetted_public_addresses", lambda addresses: addresses)

    async def resolve(hostname: str, requested_port: int) -> tuple[str, ...]:
        del hostname, requested_port
        return ("127.0.0.1",)

    for hostname, trust_fixture in (("calendar-b.example", True), ("calendar-a.example", False)):
        server, observed_sni, _observed_requests, _observed_peers = await _start_tls_calendar()
        assert server.sockets
        port = int(server.sockets[0].getsockname()[1])
        transport = HardenedCalendarTransport(
            2.0,
            resolver=resolve,
            transport=LocalTlsTransport(port, _tls_client_context(trust_fixture=trust_fixture)),
        )
        try:
            with pytest.raises(CalendarUnavailable, match="calendar_request_unavailable"):
                await transport.submit(f"https://{hostname}/", b"g" * 32)
        finally:
            server.close()
            await server.wait_closed()
        assert observed_sni == [hostname]
