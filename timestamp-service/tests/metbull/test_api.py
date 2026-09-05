from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.metbull import (
    MetbullNotFound,
    MetbullNotOfficial,
    MetbullRecord,
    MetbullUnavailable,
    MeteoriticalBulletinSnapshot,
)


class FakeLookup:
    def __init__(self, result: MetbullRecord | Exception) -> None:
        self.result = result
        self.calls: list[int] = []

    async def lookup(self, code: int) -> MetbullRecord:
        self.calls.append(code)
        if isinstance(self.result, Exception):
            raise self.result
        return replace(
            self.result,
            code=code,
            official_url=f"https://www.lpi.usra.edu/meteor/metbull.cfm?code={code}",
        )


def official_record() -> MetbullRecord:
    return MetbullRecord(
        code=87447,
        official_url="https://www.lpi.usra.edu/meteor/metbull.cfm?code=87447",
        canonical_name="Northwest Africa 18652",
        record_status="Relict",
        official_name=True,
        recommended_classification="Relict iron",
        fall_or_find="Find",
        year_found=2018,
        country=None,
        latitude=None,
        longitude=None,
    )


def test_api_returns_strict_public_contract_without_sensitive_specimen_fields(app_factory: Any) -> None:
    lookup = FakeLookup(official_record())
    context = app_factory(metbull_lookup_enabled=True)
    context.app.state.services.metbull_lookup = lookup
    with TestClient(context.app) as client:
        response = client.get(
            "/v1/meteorites/metbull?code=87447",
            headers={"Origin": "https://coa.example.test"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "code": 87447,
        "official_url": "https://www.lpi.usra.edu/meteor/metbull.cfm?code=87447",
        "canonical_name": "Northwest Africa 18652",
        "record_status": "Relict",
        "official_name": True,
        "recommended_classification": "Relict iron",
        "fall_or_find": "Find",
        "year_found": 2018,
        "country": None,
        "latitude": None,
        "longitude": None,
    }
    assert lookup.calls == [87447]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert not any(name.lower().startswith("access-control-") for name in response.headers)
    assert response.headers.get("vary") != "Origin"
    assert not {"mass", "specimen", "ownership", "finder", "provenance"} & response.json().keys()


def test_enabled_app_wires_the_bundled_snapshot() -> None:
    app = create_app(Settings(_env_file=None, app_env="test", metbull_lookup_enabled=True))
    assert isinstance(app.state.services.metbull_lookup, MeteoriticalBulletinSnapshot)
    with TestClient(app) as client:
        response = client.get("/v1/meteorites/metbull?code=87447")
    assert response.status_code == 200
    assert response.json()["canonical_name"] == "Northwest Africa 18652"
    assert response.json()["country"] is None


@pytest.mark.parametrize(
    ("query", "expected_status"),
    [
        ("", 422),
        ("?code=0", 422),
        ("?code=01", 422),
        ("?code=1000000000", 422),
        ("?code=-1", 422),
        ("?code=https%3A%2F%2Fevil.example%2F", 422),
        ("?code=87447&code=1", 422),
        ("?code=87447&host=evil.example", 422),
    ],
)
def test_api_accepts_only_one_canonical_decimal_code(query: str, expected_status: int) -> None:
    lookup = FakeLookup(official_record())
    app = create_app(
        Settings(_env_file=None, app_env="test", metbull_lookup_enabled=True),
        metbull_lookup=lookup,
    )
    with TestClient(app) as client:
        response = client.get(f"/v1/meteorites/metbull{query}")
    assert response.status_code == expected_status
    assert response.json() == {"detail": "invalid request"}
    assert lookup.calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (MetbullNotFound("internal"), 404, "meteorite record not found"),
        (MetbullNotOfficial("internal"), 409, "meteorite name is not official"),
        (MetbullUnavailable("sensitive upstream reason"), 503, "meteorite lookup unavailable"),
    ],
)
def test_api_maps_lookup_failures_to_generic_public_errors(error: Exception, status: int, detail: str) -> None:
    lookup = FakeLookup(error)
    app = create_app(
        Settings(_env_file=None, app_env="test", metbull_lookup_enabled=True),
        metbull_lookup=lookup,
    )
    with TestClient(app) as client:
        response = client.get("/v1/meteorites/metbull?code=87447")
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert "sensitive" not in response.text


def test_disabled_mode_never_calls_injected_lookup() -> None:
    lookup = FakeLookup(official_record())
    app = create_app(Settings(_env_file=None, app_env="test"), metbull_lookup=lookup)
    with TestClient(app) as client:
        response = client.get("/v1/meteorites/metbull?code=87447")
    assert response.status_code == 503
    assert response.json() == {"detail": "meteorite lookup unavailable"}
    assert lookup.calls == []
    assert app.state.services.metbull_lookup is None


def test_metbull_has_a_dedicated_rate_limit(app_factory: Any) -> None:
    lookup = FakeLookup(official_record())
    context = app_factory(metbull_lookup_enabled=True, metbull_rate_limit=1)
    context.app.state.services.metbull_lookup = lookup
    with TestClient(context.app) as client:
        first = client.get("/v1/meteorites/metbull?code=87447")
        limited = client.get("/v1/meteorites/metbull?code=87447")
    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json() == {"detail": "rate limit exceeded"}
    assert lookup.calls == [87447]
