from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.update_metbull_snapshot import EXPECTED_HEADER, SnapshotUpdateError, build_snapshot


def csv_bytes(*rows: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(EXPECTED_HEADER)
    writer.writerows(rows)
    return output.getvalue().encode()


def sentinel_row() -> list[str]:
    return [
        "87447",
        "Northwest Africa 18652",
        "NWA 18652",
        "Relict",
        "",
        "2018",
        "Western Sahara",
        "Relict iron",
        "30000.0",
        "115",
        "",
        "",
        "",
        "",
    ]


def official_row() -> list[str]:
    return [
        "1",
        "Aachen",
        "Aachen",
        "Official",
        "Y",
        "1880",
        "Germany",
        "L5",
        "21.0",
        "0",
        "",
        "50.77500",
        "6.08333",
        "",
    ]


def test_builder_validates_and_atomically_replaces_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "metbull.sqlite3"
    metadata_path = tmp_path / "metbull.json"
    database.write_bytes(b"old database")
    metadata_path.write_text("old metadata", encoding="utf-8")
    metadata = build_snapshot(
        csv_bytes(official_row(), sentinel_row()),
        database,
        metadata_path,
        retrieved_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        official_database_updated_utc=None,
        minimum_row_count=2,
    )
    assert metadata["row_count"] == 2
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == metadata
    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        assert connection.execute("SELECT code FROM records ORDER BY code").fetchall() == [(1,), (87447,)]
        row = connection.execute(
            "SELECT fall_or_find, year_found, latitude, longitude FROM records WHERE code = 1"
        ).fetchone()
        assert row == ("Fall", 1880, "50.77500", "6.08333")
        indexes = connection.execute("PRAGMA index_list(records)").fetchall()
        assert indexes == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda _rows: [],
        lambda rows: [rows[0], rows[0]],
        lambda rows: [rows[0][:-1]],
        lambda rows: [[*rows[0][:3], "New status", *rows[0][4:]]],
        lambda rows: [[*rows[0][:4], "Maybe", *rows[0][5:]]],
        lambda rows: [[*rows[0][:11], "91 north", "0", rows[0][13]]],
    ],
)
def test_builder_rejects_invalid_input_without_replacing_existing_files(
    tmp_path: Path,
    mutate: Callable[[list[list[str]]], list[list[str]]],
) -> None:
    database = tmp_path / "metbull.sqlite3"
    metadata = tmp_path / "metbull.json"
    database.write_bytes(b"old database")
    metadata.write_bytes(b"old metadata")
    rows = [sentinel_row()]
    changed = mutate(rows)
    with pytest.raises(SnapshotUpdateError):
        build_snapshot(
            csv_bytes(*changed),
            database,
            metadata,
            retrieved_at=datetime(2026, 9, 5, tzinfo=UTC),
            official_database_updated_utc=None,
            minimum_row_count=1,
        )
    assert database.read_bytes() == b"old database"
    assert metadata.read_bytes() == b"old metadata"


def test_builder_maps_nonstrict_year_and_out_of_range_coordinates_to_null(tmp_path: Path) -> None:
    row = official_row()
    row[0] = "2"
    row[5] = "1960s"
    row[11] = "91.0"
    database = tmp_path / "metbull.sqlite3"
    metadata = tmp_path / "metbull.json"
    build_snapshot(
        csv_bytes(row, sentinel_row()),
        database,
        metadata,
        retrieved_at=datetime(2026, 9, 5, tzinfo=UTC),
        official_database_updated_utc=None,
        minimum_row_count=2,
    )
    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        assert connection.execute(
            "SELECT year_found, latitude, longitude FROM records WHERE code = 2"
        ).fetchone() == (None, None, None)


def test_database_build_is_deterministic_for_the_same_source(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    source = csv_bytes(official_row(), sentinel_row())
    first_metadata = build_snapshot(
        source,
        first / "metbull.sqlite3",
        first / "metbull.json",
        retrieved_at=datetime(2026, 9, 5, tzinfo=UTC),
        official_database_updated_utc=None,
        minimum_row_count=2,
    )
    second_metadata = build_snapshot(
        source,
        second / "metbull.sqlite3",
        second / "metbull.json",
        retrieved_at=datetime(2026, 9, 6, tzinfo=UTC),
        official_database_updated_utc=None,
        minimum_row_count=2,
    )
    assert first_metadata["database_sha256"] == second_metadata["database_sha256"]
    assert (first / "metbull.sqlite3").read_bytes() == (second / "metbull.sqlite3").read_bytes()
