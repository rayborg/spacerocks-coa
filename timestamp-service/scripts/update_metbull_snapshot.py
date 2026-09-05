from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import ssl
import tempfile
import urllib.error
import urllib.request
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

SOURCE_URL = "https://www.lpi.usra.edu/meteor/metbull.cfm?sea=%25&sfor=names&stype=contains&csv=1"
EXPECTED_HEADER = (
    "Code",
    "Name",
    "Abbrev",
    "Status",
    "Fall",
    "Year",
    "Place",
    "Type",
    "Mass",
    "MetBull",
    "Antarctic",
    "Lat",
    "Long",
    "Comment",
)
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MINIMUM_ROW_COUNT = 80_000
APPLICATION_ID = 0x4D42554C
SCHEMA_VERSION = 2
ALLOWED_STATUSES = frozenset(
    {"Official", "Relict", "Provisional", "Pseudo", "Crater", "Doubtful", "Discredited", "Undocumented", "Artifact"}
)
FALL_MARKERS = frozenset({"", "Y", "Yc", "Yp", "Np", "Nd"})
FALL_VALUES = frozenset({"Y", "Yc", "Yp"})
CANONICAL_CODE = re.compile(r"^[1-9][0-9]{0,8}$")
DECIMAL_COORDINATE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
STRICT_YEAR = re.compile(r"^[0-9]{1,4}$")
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
# Reviewed against the complete bundled CSV. New Place spellings stay unmapped until
# explicitly added, so regions such as Antarctica and Northwest Africa cannot leak through.
COUNTRY_NAMES = frozenset(
    {
        "Afghanistan",
        "Algeria",
        "Angola",
        "Argentina",
        "Armenia",
        "Australia",
        "Austria",
        "Azerbaijan",
        "Bangladesh",
        "Belarus",
        "Belgium",
        "Bolivia",
        "Bosnia and Herzegovina",
        "Botswana",
        "Brazil",
        "Bulgaria",
        "Burkina Faso",
        "Cambodia",
        "Cameroon",
        "Canada",
        "Central African Republic",
        "Chad",
        "Chile",
        "China",
        "Colombia",
        "Costa Rica",
        "Croatia",
        "Cuba",
        "Czech Republic",
        "Denmark",
        "Ecuador",
        "Egypt",
        "Estonia",
        "Ethiopia",
        "Finland",
        "France",
        "Germany",
        "Ghana",
        "Greece",
        "Greenland",
        "Guatemala",
        "Honduras",
        "Hungary",
        "India",
        "Indonesia",
        "Iran",
        "Iraq",
        "Ireland",
        "Isle of Man",
        "Israel",
        "Italy",
        "Jamaica",
        "Japan",
        "Jordan",
        "Kazakhstan",
        "Kenya",
        "Laos",
        "Latvia",
        "Lebanon",
        "Lesotho",
        "Libya",
        "Lithuania",
        "Madagascar",
        "Malawi",
        "Mali",
        "Mauritania",
        "Mauritius",
        "Mexico",
        "Mongolia",
        "Morocco",
        "Namibia",
        "Netherlands",
        "New Zealand",
        "Nicaragua",
        "Niger",
        "Nigeria",
        "North Korea",
        "Norway",
        "Oman",
        "Pakistan",
        "Papua New Guinea",
        "Paraguay",
        "Peru",
        "Philippines",
        "Poland",
        "Portugal",
        "Qatar",
        "Romania",
        "Russia",
        "Rwanda",
        "Saudi Arabia",
        "Slovakia",
        "Slovenia",
        "Somalia",
        "South Africa",
        "South Korea",
        "South Sudan",
        "Spain",
        "Sri Lanka",
        "Sudan",
        "Sweden",
        "Switzerland",
        "Syria",
        "Tajikistan",
        "Tanzania",
        "Thailand",
        "Tunisia",
        "Turkey",
        "Turkmenistan",
        "Uganda",
        "Ukraine",
        "United Arab Emirates",
        "United Kingdom",
        "United States",
        "Uruguay",
        "Uzbekistan",
        "Venezuela",
        "Vietnam",
        "Western Sahara",
        "Yemen",
        "Zambia",
        "Zimbabwe",
    }
)
COUNTRY_ALIASES = {
    "Burma": "Myanmar",
    "Congo - Dem. Rep.": "Democratic Republic of the Congo",
    "Ivory Coast": "Cote d'Ivoire",
    "Swaziland": "Eswatini",
    "USA": "United States",
}
SERVICE_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = SERVICE_ROOT / "app" / "metbull" / "data" / "metbull.sqlite3"
METADATA_PATH = SERVICE_ROOT / "app" / "metbull" / "data" / "metbull.json"


class SnapshotUpdateError(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise SnapshotUpdateError("redirects are forbidden")


def download_official_csv() -> tuple[bytes, str | None]:
    tls = ssl.create_default_context()
    tls.check_hostname = True
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
        urllib.request.HTTPSHandler(context=tls),
    )
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "Accept": "text/csv",
            "Accept-Encoding": "identity",
            "User-Agent": "spacerocks-coa-metbull-snapshot/1",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=60) as response:
            if response.status != 200 or response.geturl() != SOURCE_URL:
                raise SnapshotUpdateError("official CSV returned an unexpected response")
            media_type = response.headers.get_content_type().casefold()
            if media_type != "text/csv":
                raise SnapshotUpdateError("official CSV returned an unexpected content type")
            encoding = response.headers.get("Content-Encoding")
            if encoding is not None and encoding.strip().casefold() != "identity":
                raise SnapshotUpdateError("encoded responses are forbidden")
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    content_length = int(declared_length)
                except ValueError as error:
                    raise SnapshotUpdateError("invalid Content-Length") from error
                if not 1 <= content_length <= MAX_DOWNLOAD_BYTES:
                    raise SnapshotUpdateError("official CSV exceeds the download limit")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if not body or len(body) > MAX_DOWNLOAD_BYTES:
                raise SnapshotUpdateError("official CSV is empty or exceeds the download limit")
            if declared_length is not None and len(body) != content_length:
                raise SnapshotUpdateError("official CSV length does not match Content-Length")
            updated = _http_date(response.headers.get("Last-Modified"))
    except SnapshotUpdateError:
        raise
    except (OSError, urllib.error.URLError) as error:
        raise SnapshotUpdateError("official CSV download failed") from error
    return body, updated


def build_snapshot(
    csv_bytes: bytes,
    database_path: Path,
    metadata_path: Path,
    *,
    retrieved_at: datetime,
    official_database_updated_utc: str | None,
    minimum_row_count: int = MINIMUM_ROW_COUNT,
) -> dict[str, object]:
    if database_path.parent != metadata_path.parent or not database_path.parent.is_dir():
        raise SnapshotUpdateError("snapshot destination directory is missing or inconsistent")
    rows = _validated_rows(csv_bytes, minimum_row_count=minimum_row_count)
    retrieved_at_utc = _utc_timestamp(retrieved_at)
    _validate_optional_utc_timestamp(official_database_updated_utc)
    source_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    internal_metadata = {
        "schema_version": str(SCHEMA_VERSION),
        "source_url": SOURCE_URL,
        "source_sha256": source_sha256,
        "row_count": str(len(rows)),
    }

    database_descriptor, raw_database_temp = tempfile.mkstemp(
        prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent
    )
    os.close(database_descriptor)
    database_temp = Path(raw_database_temp)
    metadata_temp: Path | None = None
    try:
        _write_database(database_temp, rows, internal_metadata)
        _validate_built_database(database_temp, len(rows))
        database_sha256 = _sha256_file(database_temp)
        metadata: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "name": "Meteoritical Bulletin Database",
                "publisher": "Lunar and Planetary Institute",
                "url": SOURCE_URL,
            },
            "retrieved_at_utc": retrieved_at_utc,
            "source_sha256": source_sha256,
            "database_sha256": database_sha256,
            "row_count": len(rows),
            "official_database_updated_utc": official_database_updated_utc,
        }
        metadata_temp = _write_metadata_temp(metadata_path, metadata)
        os.chmod(database_temp, 0o444)
        os.chmod(metadata_temp, 0o444)
        os.replace(database_temp, database_path)
        os.replace(metadata_temp, metadata_path)
        _fsync_directory(database_path.parent)
        return metadata
    finally:
        database_temp.unlink(missing_ok=True)
        if metadata_temp is not None:
            metadata_temp.unlink(missing_ok=True)


def _validated_rows(csv_bytes: bytes, *, minimum_row_count: int) -> list[tuple[object, ...]]:
    if not 1 <= len(csv_bytes) <= MAX_DOWNLOAD_BYTES:
        raise SnapshotUpdateError("CSV size is outside bounds")
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SnapshotUpdateError("CSV is not valid UTF-8") from error
    if "\x00" in text:
        raise SnapshotUpdateError("CSV contains a NUL byte")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as error:
        raise SnapshotUpdateError("CSV header is missing") from error
    if tuple(header) != EXPECTED_HEADER:
        raise SnapshotUpdateError("CSV header does not match the official 14-column schema")

    records: list[tuple[object, ...]] = []
    seen_codes: set[int] = set()
    sentinel: tuple[object, ...] | None = None
    try:
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(EXPECTED_HEADER):
                raise SnapshotUpdateError(f"CSV row {row_number} does not contain 14 columns")
            record = _validated_row(row, row_number)
            code = record[0]
            assert isinstance(code, int)
            if code in seen_codes:
                raise SnapshotUpdateError(f"CSV row {row_number} repeats canonical code {code}")
            seen_codes.add(code)
            records.append(record)
            if code == 87447:
                sentinel = record
    except csv.Error as error:
        raise SnapshotUpdateError(f"CSV parsing failed at row {reader.line_num}") from error
    if len(records) < minimum_row_count:
        raise SnapshotUpdateError(f"CSV contains fewer than {minimum_row_count} records")
    expected_sentinel = (
        87447,
        "Northwest Africa 18652",
        "Relict",
        "Relict iron",
        "Find",
        2018,
        "Western Sahara",
        None,
        None,
    )
    if sentinel != expected_sentinel:
        raise SnapshotUpdateError("CSV sentinel 87447 is missing or invalid")
    records.sort(key=lambda record: int(record[0]))
    return records


def _validated_row(row: Sequence[str], row_number: int) -> tuple[object, ...]:
    raw_code, name, _abbrev, status, fall, year, place, classification, *_remainder = row
    latitude, longitude = row[11], row[12]
    if CANONICAL_CODE.fullmatch(raw_code) is None:
        raise SnapshotUpdateError(f"CSV row {row_number} has a noncanonical code")
    code = int(raw_code)
    _bounded_text(name, 128, row_number, "Name")
    _bounded_text(classification, 128, row_number, "Type")
    if status not in ALLOWED_STATUSES:
        raise SnapshotUpdateError(f"CSV row {row_number} has an unknown Status")
    if fall not in FALL_MARKERS:
        raise SnapshotUpdateError(f"CSV row {row_number} has an unknown Fall marker")
    year_found = _year_or_none(year)
    coordinates = _coordinates_or_none(latitude, longitude, row_number)
    return (
        code,
        name,
        status,
        classification,
        "Fall" if fall in FALL_VALUES else "Find",
        year_found,
        _country_or_none(place),
        coordinates[0],
        coordinates[1],
    )


def _bounded_text(value: str, maximum: int, row_number: int, field: str) -> None:
    if not 1 <= len(value) <= maximum or any(ord(character) < 0x20 for character in value):
        raise SnapshotUpdateError(f"CSV row {row_number} has an invalid {field}")


def _year_or_none(value: str) -> int | None:
    if STRICT_YEAR.fullmatch(value) is None:
        return None
    year = int(value)
    return year if 1 <= year <= 9999 else None


def _country_or_none(place: str) -> str | None:
    _, separator, suffix = place.rpartition(", ")
    candidate = suffix if separator else place
    if candidate in COUNTRY_NAMES:
        return candidate
    return COUNTRY_ALIASES.get(candidate)


def _coordinates_or_none(latitude: str, longitude: str, row_number: int) -> tuple[str | None, str | None]:
    if not latitude and not longitude:
        return None, None
    if not latitude or not longitude:
        raise SnapshotUpdateError(f"CSV row {row_number} has incomplete coordinates")
    parsed_latitude = _coordinate(latitude, Decimal("-90"), Decimal("90"), row_number)
    parsed_longitude = _coordinate(longitude, Decimal("-180"), Decimal("180"), row_number)
    if parsed_latitude is None or parsed_longitude is None:
        return None, None
    return parsed_latitude, parsed_longitude


def _coordinate(value: str, minimum: Decimal, maximum: Decimal, row_number: int) -> str | None:
    if len(value) > 32 or DECIMAL_COORDINATE.fullmatch(value) is None:
        raise SnapshotUpdateError(f"CSV row {row_number} has invalid coordinates")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SnapshotUpdateError(f"CSV row {row_number} has invalid coordinates") from error
    return value if minimum <= parsed <= maximum else None


def _write_database(
    path: Path,
    rows: Sequence[tuple[object, ...]],
    metadata: dict[str, str],
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = {SCHEMA_VERSION};
            PRAGMA page_size = 4096;
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            CREATE TABLE records (
                code INTEGER PRIMARY KEY CHECK(code BETWEEN 1 AND 999999999),
                canonical_name TEXT NOT NULL CHECK(length(canonical_name) BETWEEN 1 AND 128),
                record_status TEXT NOT NULL CHECK(record_status IN (
                    'Official', 'Relict', 'Provisional', 'Pseudo', 'Crater', 'Doubtful',
                    'Discredited', 'Undocumented', 'Artifact'
                )),
                recommended_classification TEXT NOT NULL
                    CHECK(length(recommended_classification) BETWEEN 1 AND 128),
                fall_or_find TEXT NOT NULL CHECK(fall_or_find IN ('Fall', 'Find')),
                year_found INTEGER CHECK(year_found BETWEEN 1 AND 9999),
                country TEXT CHECK(length(country) BETWEEN 1 AND 64),
                latitude TEXT,
                longitude TEXT,
                CHECK((latitude IS NULL) = (longitude IS NULL))
            ) STRICT;
            CREATE INDEX records_country_idx ON records(country) WHERE country IS NOT NULL;
            CREATE TABLE snapshot_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) STRICT, WITHOUT ROWID;
            """
        )
        with connection:
            connection.executemany(
                """
                INSERT INTO records (
                    code, canonical_name, record_status, recommended_classification,
                    fall_or_find, year_found, country, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.executemany(
                "INSERT INTO snapshot_metadata (key, value) VALUES (?, ?)", sorted(metadata.items())
            )
        connection.execute("VACUUM")
    finally:
        connection.close()
    with path.open("rb") as database:
        os.fsync(database.fileno())


def _validate_built_database(path: Path, row_count: int) -> None:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise SnapshotUpdateError("generated snapshot failed SQLite integrity validation")
        if connection.execute("SELECT count(*) FROM records").fetchone() != (row_count,):
            raise SnapshotUpdateError("generated snapshot row count is invalid")
        sentinel = connection.execute(
            """
            SELECT canonical_name, record_status, recommended_classification,
                   fall_or_find, year_found, country, latitude, longitude
            FROM records WHERE code = 87447
            """
        ).fetchone()
        if sentinel != (
            "Northwest Africa 18652",
            "Relict",
            "Relict iron",
            "Find",
            2018,
            "Western Sahara",
            None,
            None,
        ):
            raise SnapshotUpdateError("generated snapshot sentinel is invalid")


def _write_metadata_temp(path: Path, metadata: dict[str, object]) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(metadata, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as snapshot:
        for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _http_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as error:
        raise SnapshotUpdateError("invalid Last-Modified header") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return _utc_timestamp(parsed)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise SnapshotUpdateError("retrieval timestamp must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_optional_utc_timestamp(value: str | None) -> None:
    if value is None:
        return
    if UTC_TIMESTAMP.fullmatch(value) is None:
        raise SnapshotUpdateError("official database update timestamp is invalid")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise SnapshotUpdateError("official database update timestamp is invalid") from error


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes, official_updated = download_official_csv()
    metadata = build_snapshot(
        csv_bytes,
        DATABASE_PATH,
        METADATA_PATH,
        retrieved_at=datetime.now(UTC),
        official_database_updated_utc=official_updated,
    )
    print(
        "updated Meteoritical Bulletin snapshot: "
        f"rows={metadata['row_count']} source_sha256={metadata['source_sha256']} "
        f"database_sha256={metadata['database_sha256']}"
    )


if __name__ == "__main__":
    main()
