from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

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
            parsed = urlsplit(raw_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.port not in {None, 443}
            ):
                raise ValueError("calendar_allowlist_requires_operator_https_urls")
            base_url = raw_url.rstrip("/") + "/"
            if base_url in normalized:
                raise ValueError("duplicate_calendar_url")
            normalized.append(base_url)
            hosts.add(parsed.hostname.lower())
        if len(normalized) < 2 or len(hosts) < 2:
            raise ValueError("at_least_two_independent_calendar_hosts_required")
        if len(normalized) > MAX_CALENDAR_URLS:
            raise ValueError("calendar_allowlist_too_large")
        return tuple(normalized)


class HardenedCalendarTransport:
    def __init__(self, timeout_seconds: float) -> None:
        del timeout_seconds
        raise CalendarUnavailable("live_calendar_transport_requires_pinned_ip_tls_sni")

    async def submit(self, base_url: str, digest: bytes) -> bytes:
        del base_url, digest
        raise CalendarUnavailable("live_calendar_transport_requires_pinned_ip_tls_sni")

    async def upgrade(self, base_url: str, commitment: bytes) -> bytes | None:
        del base_url, commitment
        raise CalendarUnavailable("live_calendar_transport_requires_pinned_ip_tls_sni")


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
        if transport is None:
            raise CalendarUnavailable("live_calendar_transport_requires_pinned_ip_tls_sni")
        self._transport = transport
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
