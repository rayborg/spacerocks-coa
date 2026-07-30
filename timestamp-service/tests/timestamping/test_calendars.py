from __future__ import annotations

import asyncio

import pytest
from opentimestamps.core.notary import PendingAttestation
from opentimestamps.core.op import OpAppend
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import Timestamp

from app.domain.digest import ManifestDigest
from app.timestamping.calendars import (
    MAX_CONCURRENT_CALENDAR_REQUESTS,
    CalendarConfiguration,
    CalendarUnavailable,
    MultiCalendarTimestamper,
)
from app.timestamping.detached import deserialize_exact_proof, new_detached_exact_digest, serialize_detached


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
    with pytest.raises(CalendarUnavailable, match="requires_pinned_ip_tls_sni"):
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
