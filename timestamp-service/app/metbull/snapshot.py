from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from app.metbull.client import (
    METBULL_OFFICIAL_URL,
    MetbullNotFound,
    MetbullNotOfficial,
    MetbullRecord,
    MetbullUnavailable,
    validated_code,
)

SOURCE_URL = "https://www.lpi.usra.edu/meteor/metbull.cfm?sea=%25&sfor=names&stype=contains&csv=1"
BUNDLED_DATABASE_PATH = Path(__file__).with_name("data") / "metbull.sqlite3"
BUNDLED_METADATA_PATH = Path(__file__).with_name("data") / "metbull.json"
_APPLICATION_ID = 0x4D42554C
_SCHEMA_VERSION = 1
_MINIMUM_ROWS = 80_000
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_METADATA_KEYS = {
    "schema_version",
    "source",
    "retrieved_at_utc",
    "source_sha256",
    "database_sha256",
    "row_count",
    "official_database_updated_utc",
}
_SOURCE_KEYS = {"name", "publisher", "url"}
_INTERNAL_METADATA_KEYS = {
    "schema_version",
    "source_url",
    "source_sha256",
    "row_count",
}


class MeteoriticalBulletinSnapshot:
    """Read the bundled, validated Meteoritical Bulletin snapshot without network access."""

    def __init__(
        self,
        database_path: Path = BUNDLED_DATABASE_PATH,
        metadata_path: Path = BUNDLED_METADATA_PATH,
    ) -> None:
        self._database_path = database_path.resolve()
        self._metadata_path = metadata_path.resolve()
        self._uri = f"{self._database_path.as_uri()}?mode=ro&immutable=1"
        try:
            metadata = self._load_metadata()
            self._validate_database(metadata)
        except MetbullUnavailable:
            raise
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError) as error:
            raise MetbullUnavailable("metbull_snapshot_invalid") from error

    async def lookup(self, code: int) -> MetbullRecord:
        canonical_code = validated_code(code)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT canonical_name, record_status, recommended_classification,
                           fall_or_find, year_found, latitude, longitude
                    FROM records WHERE code = ?
                    """,
                    (canonical_code,),
                ).fetchone()
        except sqlite3.DatabaseError as error:
            raise MetbullUnavailable("metbull_snapshot_unavailable") from error
        if row is None:
            raise MetbullNotFound("metbull_record_not_found")

        name, status, classification, fall_or_find, year, latitude, longitude = cast(
            tuple[object, object, object, object, object, object, object], row
        )
        if not isinstance(status, str):
            raise MetbullUnavailable("metbull_snapshot_record_invalid")
        if status not in {"Official", "Relict"}:
            raise MetbullNotOfficial("metbull_name_not_official")
        canonical_name = _bounded_text(name, 128)
        recommended_classification = _bounded_text(classification, 128)
        if not isinstance(fall_or_find, str) or fall_or_find not in {"Fall", "Find"}:
            raise MetbullUnavailable("metbull_snapshot_record_invalid")
        year_found = _validated_year(year)
        coordinates = _validated_coordinates(latitude, longitude)
        return MetbullRecord(
            code=canonical_code,
            official_url=METBULL_OFFICIAL_URL.format(code=canonical_code),
            canonical_name=canonical_name,
            record_status=status,
            official_name=True,
            recommended_classification=recommended_classification,
            fall_or_find=fall_or_find,
            year_found=year_found,
            country=None,
            latitude=coordinates[0],
            longitude=coordinates[1],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._uri, uri=True, timeout=1.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
        except sqlite3.DatabaseError:
            connection.close()
            raise
        return connection

    def _load_metadata(self) -> dict[str, object]:
        if not self._metadata_path.is_file() or not self._database_path.is_file():
            raise MetbullUnavailable("metbull_snapshot_missing")
        if self._metadata_path.stat().st_size > 64 * 1024:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        raw: object = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != _METADATA_KEYS:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        metadata = cast(dict[str, object], raw)
        source = metadata["source"]
        if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        source_values = cast(dict[str, object], source)
        if source_values != {
            "name": "Meteoritical Bulletin Database",
            "publisher": "Lunar and Planetary Institute",
            "url": SOURCE_URL,
        }:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        if metadata["schema_version"] != _SCHEMA_VERSION:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        row_count = metadata["row_count"]
        if type(row_count) is not int or row_count < _MINIMUM_ROWS:
            raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        for key in ("source_sha256", "database_sha256"):
            value = metadata[key]
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
        _validate_utc_timestamp(metadata["retrieved_at_utc"], nullable=False)
        _validate_utc_timestamp(metadata["official_database_updated_utc"], nullable=True)
        if _sha256_file(self._database_path) != metadata["database_sha256"]:
            raise MetbullUnavailable("metbull_snapshot_hash_mismatch")
        return metadata

    def _validate_database(self, metadata: dict[str, object]) -> None:
        with closing(self._connect()) as connection:
            if connection.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,):
                raise MetbullUnavailable("metbull_snapshot_schema_invalid")
            if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
                raise MetbullUnavailable("metbull_snapshot_schema_invalid")
            objects = connection.execute(
                "SELECT name, type FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            if objects != [("records", "table"), ("snapshot_metadata", "table")]:
                raise MetbullUnavailable("metbull_snapshot_schema_invalid")
            columns = connection.execute("PRAGMA table_info(records)").fetchall()
            expected_columns = [
                ("code", "INTEGER", 0, 1),
                ("canonical_name", "TEXT", 1, 0),
                ("record_status", "TEXT", 1, 0),
                ("recommended_classification", "TEXT", 1, 0),
                ("fall_or_find", "TEXT", 1, 0),
                ("year_found", "INTEGER", 0, 0),
                ("latitude", "TEXT", 0, 0),
                ("longitude", "TEXT", 0, 0),
            ]
            actual_columns = [(row[1], row[2], row[3], row[5]) for row in columns]
            if actual_columns != expected_columns:
                raise MetbullUnavailable("metbull_snapshot_schema_invalid")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise MetbullUnavailable("metbull_snapshot_integrity_invalid")
            internal_rows = connection.execute("SELECT key, value FROM snapshot_metadata").fetchall()
            internal = {key: value for key, value in internal_rows}
            if set(internal) != _INTERNAL_METADATA_KEYS:
                raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
            expected_internal = {
                "schema_version": str(metadata["schema_version"]),
                "source_url": SOURCE_URL,
                "source_sha256": metadata["source_sha256"],
                "row_count": str(metadata["row_count"]),
            }
            if internal != expected_internal:
                raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
            if connection.execute("SELECT count(*) FROM records").fetchone() != (metadata["row_count"],):
                raise MetbullUnavailable("metbull_snapshot_row_count_invalid")
            sentinel = connection.execute(
                """
                SELECT canonical_name, record_status, recommended_classification,
                       fall_or_find, year_found, latitude, longitude
                FROM records WHERE code = 87447
                """
            ).fetchone()
            if sentinel != ("Northwest Africa 18652", "Relict", "Relict iron", "Find", 2018, None, None):
                raise MetbullUnavailable("metbull_snapshot_sentinel_invalid")


def _bounded_text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise MetbullUnavailable("metbull_snapshot_record_invalid")
    return value


def _validated_year(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 9999:
        raise MetbullUnavailable("metbull_snapshot_record_invalid")
    return value


def _validated_coordinates(latitude: object, longitude: object) -> tuple[str | None, str | None]:
    if latitude is None and longitude is None:
        return None, None
    if not isinstance(latitude, str) or not isinstance(longitude, str):
        raise MetbullUnavailable("metbull_snapshot_record_invalid")
    return _coordinate(latitude, Decimal("-90"), Decimal("90")), _coordinate(
        longitude, Decimal("-180"), Decimal("180")
    )


def _coordinate(value: str, minimum: Decimal, maximum: Decimal) -> str:
    if len(value) > 32 or _DECIMAL.fullmatch(value) is None:
        raise MetbullUnavailable("metbull_snapshot_record_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise MetbullUnavailable("metbull_snapshot_record_invalid") from error
    if not minimum <= parsed <= maximum:
        raise MetbullUnavailable("metbull_snapshot_record_invalid")
    return value


def _validate_utc_timestamp(value: object, *, nullable: bool) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise MetbullUnavailable("metbull_snapshot_metadata_invalid")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as snapshot:
        for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
