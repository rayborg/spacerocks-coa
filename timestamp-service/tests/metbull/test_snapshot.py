from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest

from app.metbull import MetbullNotFound, MetbullNotOfficial, MetbullUnavailable, MeteoriticalBulletinSnapshot
from app.metbull.snapshot import BUNDLED_DATABASE_PATH, BUNDLED_METADATA_PATH


@pytest.mark.asyncio
async def test_bundled_snapshot_returns_expected_relict_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_connect(_socket: socket.socket, _address: object) -> None:
        raise AssertionError("snapshot lookup must not open network sockets")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    lookup = MeteoriticalBulletinSnapshot()
    record = await lookup.lookup(87447)
    assert record.code == 87447
    assert record.official_url == "https://www.lpi.usra.edu/meteor/metbull.cfm?code=87447"
    assert record.canonical_name == "Northwest Africa 18652"
    assert record.record_status == "Relict"
    assert record.official_name is True
    assert record.recommended_classification == "Relict iron"
    assert record.fall_or_find == "Find"
    assert record.year_found == 2018
    assert record.country is None
    assert record.latitude is None
    assert record.longitude is None
    assert not hasattr(record, "mass")
    assert not hasattr(record, "place")
    assert not hasattr(record, "provenance")


@pytest.mark.asyncio
async def test_snapshot_distinguishes_unknown_and_nonofficial_codes() -> None:
    lookup = MeteoriticalBulletinSnapshot()
    metadata = json.loads(BUNDLED_METADATA_PATH.read_text(encoding="utf-8"))
    assert metadata["row_count"] >= 80_000
    with pytest.raises(MetbullNotFound):
        await lookup.lookup(999_999_999)
    with pytest.raises(MetbullNotOfficial):
        await lookup.lookup(3)


@pytest.mark.parametrize("code", [True, "87447", 0, -1, 1_000_000_000])
@pytest.mark.asyncio
async def test_snapshot_rejects_noncanonical_numeric_codes(code: object) -> None:
    with pytest.raises(ValueError):
        await MeteoriticalBulletinSnapshot().lookup(code)  # type: ignore[arg-type]


def test_snapshot_is_immutable_read_only_and_code_indexed() -> None:
    uri = f"{BUNDLED_DATABASE_PATH.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN SELECT * FROM records WHERE code = ?", (87447,)).fetchone()
        assert plan is not None and "INTEGER PRIMARY KEY" in plan[3]
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE records SET canonical_name = 'changed' WHERE code = 87447")


def test_missing_corrupt_or_mismatched_snapshot_fails_safely(tmp_path: Path) -> None:
    missing_database = tmp_path / "missing.sqlite3"
    missing_metadata = tmp_path / "missing.json"
    with pytest.raises(MetbullUnavailable):
        MeteoriticalBulletinSnapshot(missing_database, missing_metadata)

    corrupt_database = tmp_path / "corrupt.sqlite3"
    corrupt_database.write_bytes(b"not sqlite")
    corrupt_metadata = tmp_path / "corrupt.json"
    corrupt_metadata.write_text(BUNDLED_METADATA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MetbullUnavailable):
        MeteoriticalBulletinSnapshot(corrupt_database, corrupt_metadata)

    copied_database = tmp_path / "copied.sqlite3"
    copied_database.write_bytes(BUNDLED_DATABASE_PATH.read_bytes())
    mismatched = json.loads(BUNDLED_METADATA_PATH.read_text(encoding="utf-8"))
    mismatched["source_sha256"] = "0" * 64
    mismatched_metadata = tmp_path / "mismatched.json"
    mismatched_metadata.write_text(json.dumps(mismatched), encoding="utf-8")
    with pytest.raises(MetbullUnavailable):
        MeteoriticalBulletinSnapshot(copied_database, mismatched_metadata)
