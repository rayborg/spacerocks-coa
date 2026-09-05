from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from opentimestamps.core.notary import PendingAttestation
from opentimestamps.core.serialize import BytesDeserializationContext
from opentimestamps.core.timestamp import Timestamp

from app.domain.digest import ManifestDigest
from app.ports.timestamping import PendingProof
from app.timestamping.detached import (
    deserialize_exact_proof,
    new_detached_exact_digest,
    serialize_detached,
    validate_timestamp_tree,
)

CALENDAR_RESPONSE_LIMIT = 10_000
MAX_CALENDAR_URLS = 8
MAX_UPGRADE_REQUESTS = 8
MAX_CONCURRENT_CALENDAR_REQUESTS = 4


class CalendarUnavailable(RuntimeError):
    pass


class CalendarTransport(Protocol):
    async def submit(self, base_url: str, digest: bytes) -> bytes: ...

    async def upgrade(self, base_url: str, commitment: bytes) -> bytes | None: ...


class CalendarResolver(Protocol):
    async def __call__(self, hostname: str, port: int) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class CalendarConfiguration:
    allowlist: tuple[str, ...] = ()
    enabled: bool = False
    timeout_seconds: float = 5.0

    def validated_urls(self) -> tuple[str, ...]:
        if not self.enabled:
            raise ValueError("real_calendars_disabled")
        if not 0.1 <= self.timeout_seconds <= 30.0:
            raise ValueError("calendar_timeout_out_of_bounds")
        normalized: list[str] = []
        hosts: set[str] = set()
        for raw_url in self.allowlist:
            base_url, hostname = _validated_calendar_url(raw_url)
            if base_url in normalized:
                raise ValueError("duplicate_calendar_url")
            normalized.append(base_url)
            hosts.add(hostname)
        if len(normalized) < 2 or len(hosts) < 2:
            raise ValueError("at_least_two_independent_calendar_hosts_required")
        if len(normalized) > MAX_CALENDAR_URLS:
            raise ValueError("calendar_allowlist_too_large")
        return tuple(normalized)


class HardenedCalendarTransport:
    """Resolve once, reject the entire unsafe DNS snapshot, then connect to one pinned IP."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        resolver: CalendarResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ValueError("calendar_timeout_out_of_bounds")
        self._timeout_seconds = timeout_seconds
        self._timeout = httpx.Timeout(timeout_seconds)
        self._resolver = resolver or _resolve_hostname
        self._transport = transport

    async def submit(self, base_url: str, digest: bytes) -> bytes:
        if len(digest) != 32:
            raise CalendarUnavailable("calendar_digest_invalid")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._request("POST", base_url, "digest", content=digest)
        except TimeoutError as exc:
            raise CalendarUnavailable("calendar_request_unavailable") from exc
        if response is None:
            raise CalendarUnavailable("calendar_response_invalid")
        return response

    async def upgrade(self, base_url: str, commitment: bytes) -> bytes | None:
        if len(commitment) != 32:
            raise CalendarUnavailable("calendar_commitment_invalid")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._request(
                    "GET",
                    base_url,
                    f"timestamp/{commitment.hex()}",
                    allow_not_found=True,
                )
        except TimeoutError as exc:
            raise CalendarUnavailable("calendar_request_unavailable") from exc

    async def _request(
        self,
        method: str,
        base_url: str,
        suffix: str,
        *,
        content: bytes = b"",
        allow_not_found: bool = False,
    ) -> bytes | None:
        normalized, hostname = _validated_calendar_url(base_url)
        try:
            addresses = await self._resolver(hostname, 443)
        except (OSError, TimeoutError) as exc:
            raise CalendarUnavailable("calendar_dns_unavailable") from exc
        vetted = _vetted_public_addresses(addresses)
        pinned = ipaddress.ip_address(vetted[0])
        authority = f"[{pinned.compressed}]" if pinned.version == 6 else pinned.compressed
        path = urlsplit(normalized).path + suffix
        request = httpx.Request(
            method,
            urlunsplit(("https", authority, path, "", "")),
            headers={
                "Host": hostname,
                "Accept": "application/vnd.opentimestamps.v1",
                "Accept-Encoding": "identity",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=content,
            extensions={"sni_hostname": hostname},
        )
        body = bytearray()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.send(request, stream=True)
                try:
                    if allow_not_found and response.status_code == 404:
                        return None
                    if response.status_code != 200:
                        raise CalendarUnavailable("calendar_http_response_invalid")
                    content_encoding = response.headers.get("content-encoding")
                    if content_encoding is not None and content_encoding.strip().lower() != "identity":
                        raise CalendarUnavailable("calendar_response_encoding_invalid")
                    async for chunk in response.aiter_raw():
                        if len(chunk) > CALENDAR_RESPONSE_LIMIT - len(body):
                            raise CalendarUnavailable("calendar_response_invalid")
                        body.extend(chunk)
                finally:
                    await response.aclose()
        except CalendarUnavailable:
            raise
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise CalendarUnavailable("calendar_request_unavailable") from exc
        if not body:
            raise CalendarUnavailable("calendar_response_invalid")
        return bytes(body)


async def _resolve_hostname(hostname: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return tuple(record[4][0] for record in records)


def _vetted_public_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        raise CalendarUnavailable("calendar_dns_empty")
    normalized: list[str] = []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise CalendarUnavailable("calendar_dns_address_invalid") from exc
        if not _is_globally_routable_unicast(address):
            raise CalendarUnavailable("calendar_dns_address_not_public")
        value = address.compressed
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


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


def _validated_calendar_url(raw_url: str) -> tuple[str, str]:
    if (
        not raw_url
        or raw_url != raw_url.strip()
        or not raw_url.isascii()
        or "\\" in raw_url
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw_url)
    ):
        raise ValueError("calendar_allowlist_requires_operator_https_urls")
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("calendar_allowlist_requires_operator_https_urls") from exc
    hostname = (parsed.hostname or "").lower()
    labels = hostname.split(".")
    hostname_valid = (
        len(hostname) <= 253
        and len(labels) >= 2
        and all(
            1 <= len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        )
    )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = False
    else:
        literal_ip = True
    path_invalid = bool(parsed.path) and not parsed.path.startswith("/")
    if (
        parsed.scheme != "https"
        or not hostname_valid
        or literal_ip
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
        or path_invalid
    ):
        raise ValueError("calendar_allowlist_requires_operator_https_urls")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit(("https", hostname, path, "", "")), hostname


def _timestamp_from_bytes(message: bytes, response: bytes) -> Timestamp:
    if not response or len(response) > CALENDAR_RESPONSE_LIMIT:
        raise CalendarUnavailable("calendar_response_invalid")
    try:
        context = BytesDeserializationContext(response)
        timestamp = Timestamp.deserialize(context, message)
        context.assert_eof()
        validate_timestamp_tree(timestamp)
    except Exception as exc:
        raise CalendarUnavailable("calendar_response_invalid") from exc
    return timestamp


class MultiCalendarTimestamper:
    """Explicitly enabled real adapter; callers cannot supply per-request URLs."""

    def __init__(self, config: CalendarConfiguration, transport: CalendarTransport | None = None) -> None:
        self._urls = config.validated_urls()
        self._transport = transport or HardenedCalendarTransport(config.timeout_seconds)
        self._request_slots = asyncio.Semaphore(MAX_CONCURRENT_CALENDAR_REQUESTS)

    async def stamp_exact_digest(self, digest: ManifestDigest) -> PendingProof:
        target = digest.ots_target()
        responses = await asyncio.gather(
            *(self._submit(url, target) for url in self._urls),
            return_exceptions=True,
        )
        detached = new_detached_exact_digest(digest)
        merged = 0
        for response in responses:
            if isinstance(response, bytes):
                try:
                    detached.timestamp.merge(_timestamp_from_bytes(target, response))
                except (CalendarUnavailable, ValueError):
                    continue
                merged += 1
        if merged == 0:
            raise CalendarUnavailable("all_calendars_unavailable")
        proof_bytes = serialize_detached(detached)
        deserialize_exact_proof(digest, proof_bytes)
        return PendingProof(proof_bytes=proof_bytes, calendar_submitted_at=datetime.now(UTC))

    async def upgrade_exact_digest(self, digest: ManifestDigest, proof_bytes: bytes) -> bytes:
        parsed = deserialize_exact_proof(digest, proof_bytes)
        allowed = set(self._urls)
        pending_by_key = {
            (node.msg, attestation.uri.rstrip("/") + "/"): node
            for node in _walk(parsed.detached.timestamp)
            for attestation in node.attestations
            if type(attestation) is PendingAttestation and attestation.uri.rstrip("/") + "/" in allowed
        }
        if len(pending_by_key) > MAX_UPGRADE_REQUESTS:
            raise CalendarUnavailable("calendar_upgrade_fanout_exceeded")
        pending = [(node, key[1]) for key, node in pending_by_key.items()]
        if not pending:
            return proof_bytes
        responses = await asyncio.gather(
            *(self._upgrade(url, node.msg) for node, url in pending),
            return_exceptions=True,
        )
        changed = False
        for (node, _url), response in zip(pending, responses, strict=True):
            if isinstance(response, bytes):
                remote = _timestamp_from_bytes(node.msg, response)
                node.merge(remote)
                changed = True
        upgraded = serialize_detached(parsed.detached) if changed else proof_bytes
        deserialize_exact_proof(digest, upgraded)
        return upgraded

    async def _submit(self, url: str, target: bytes) -> bytes:
        async with self._request_slots:
            return await self._transport.submit(url, target)

    async def _upgrade(self, url: str, message: bytes) -> bytes | None:
        async with self._request_slots:
            return await self._transport.upgrade(url, message)


def _walk(root: Timestamp) -> Iterable[Timestamp]:
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.ops.values())
