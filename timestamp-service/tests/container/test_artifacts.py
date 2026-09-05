from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_is_nonroot_bounded_and_health_checked() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    gcloudignore = (ROOT / ".gcloudignore").read_text()
    assert "python:3.12.11-slim-bookworm" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "--limit-concurrency" in dockerfile
    assert "--no-access-log" in dockerfile
    assert "COPY ." not in dockerfile
    assert "COPY --chown=timestamp:timestamp app ./app" in dockerfile
    assert "!app/metbull/data/metbull.sqlite3" in dockerignore
    assert "!app/metbull/data/metbull.sqlite3" in gcloudignore
    assert (ROOT / "app/metbull/data/metbull.sqlite3").stat().st_size > 0


def test_compose_separates_api_worker_and_requires_local_credentials() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    assert "  api:" in compose
    assert "  worker:" in compose
    assert "127.0.0.1:8000:8000" in compose
    assert "POSTGRES_PASSWORD:?" in compose
    assert "read_only: true" in compose


def test_railway_uses_health_check_and_separate_worker_command() -> None:
    railway = (ROOT / "railway.toml").read_text()
    assert 'healthcheckPath = "/health/ready"' in railway
    assert "python -m app.worker.cli" in railway
