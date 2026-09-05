from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.security.http import _safe_path


def test_metbull_settings_are_disabled_and_bounded_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.metbull_lookup_enabled is False
    assert settings.metbull_timeout_seconds == 8.0
    assert settings.metbull_rate_limit == 30
    assert _safe_path("/v1/meteorites/metbull") == "/v1/meteorites/metbull"


@pytest.mark.parametrize(
    "override",
    [
        {"metbull_timeout_seconds": 0.09},
        {"metbull_timeout_seconds": 30.1},
        {"metbull_rate_limit": 0},
    ],
)
def test_metbull_settings_reject_unbounded_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **override)
