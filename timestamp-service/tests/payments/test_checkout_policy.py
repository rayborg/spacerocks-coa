from __future__ import annotations

import base64
from typing import Any

import pytest
from conftest import ServiceContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Order
from app.payments.models import HostedCheckoutRequest


def _idempotency_key() -> str:
    return base64.urlsafe_b64encode(b"policy-check-key").rstrip(b"=").decode("ascii")


def _payload(terms_version: str, privacy_version: str) -> dict[str, object]:
    return {
        "certificate_reference": "AZ-2019-0447-HE",
        "manifest_sha256": "cf0a31b01661599b8f73cd2dd2830f859e36a00c8ca22b259b33a7ec32c067cc",
        "email": "customer@example.test",
        "consent": {
            "managed_timestamp": True,
            "terms_version": terms_version,
            "privacy_version": privacy_version,
            "accepted_at": "2026-08-27T12:00:00Z",
        },
    }


@pytest.mark.parametrize(
    ("terms_version", "privacy_version"),
    [("outdated-terms", "privacy-2026-08"), ("terms-2026-08", "outdated-privacy")],
)
def test_checkout_rejects_unapproved_policy_before_order_or_provider_call(
    app_factory: Any,
    terms_version: str,
    privacy_version: str,
) -> None:
    context: ServiceContext = app_factory(
        expected_terms_version="terms-2026-08",
        expected_privacy_version="privacy-2026-08",
    )
    provider_calls = 0

    async def provider_must_not_run(_request: HostedCheckoutRequest, _key: str) -> None:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run for an unapproved policy version")

    context.provider.create_checkout = provider_must_not_run  # type: ignore[method-assign]
    with TestClient(context.app) as client:
        response = client.post(
            "/v1/checkout",
            json=_payload(terms_version, privacy_version),
            headers={"Idempotency-Key": _idempotency_key()},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid request"}
    assert provider_calls == 0
    with context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_checkout_persists_exact_approved_policy_versions(app_factory: Any) -> None:
    context: ServiceContext = app_factory(
        expected_terms_version="terms-2026-08",
        expected_privacy_version="privacy-2026-08",
    )
    with TestClient(context.app) as client:
        response = client.post(
            "/v1/checkout",
            json=_payload("terms-2026-08", "privacy-2026-08"),
            headers={"Idempotency-Key": _idempotency_key()},
        )

    assert response.status_code == 201
    with context.session_factory() as session:
        order = session.scalar(select(Order))
        assert order is not None
        assert order.consent_terms_version == "terms-2026-08"
        assert order.consent_privacy_version == "privacy-2026-08"
