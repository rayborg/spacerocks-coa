from __future__ import annotations

import copy
import hashlib
import json

import pytest
from conftest import REPOSITORY_ROOT
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

CONTRACTS = REPOSITORY_ROOT / "contracts"


def load(relative: str) -> dict[str, object]:
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("schema_name", "fixture_name"),
    [
        ("checkout-request", "checkout-request.valid"),
        ("checkout-response", "checkout-response.valid"),
        ("order-status", "order-status.valid"),
        ("order-status", "order-status.bitcoin-verified-pending-bundle.valid"),
        ("timestamp-receipt", "timestamp-receipt.pending.valid"),
        ("timestamp-receipt", "timestamp-receipt.verified.valid"),
        ("known-manifest-fixture", "known-manifest"),
        ("rotate-token-response", "rotate-token-response.valid"),
    ],
)
def test_every_json_fixture_validates(schema_name: str, fixture_name: str) -> None:
    validator = Draft202012Validator(load(f"schemas/{schema_name}.schema.json"), format_checker=FormatChecker())
    validator.validate(load(f"fixtures/{fixture_name}.json"))


def test_checkout_rejects_unknown_uppercase_digest_and_oversized_fields() -> None:
    validator = Draft202012Validator(load("schemas/checkout-request.schema.json"), format_checker=FormatChecker())
    valid = load("fixtures/checkout-request.valid.json")
    mutations = []
    unknown = copy.deepcopy(valid)
    unknown["manifest"] = {}
    mutations.append(unknown)
    uppercase = copy.deepcopy(valid)
    uppercase["manifest_sha256"] = "A" * 64
    mutations.append(uppercase)
    certificate = copy.deepcopy(valid)
    certificate["certificate_reference"] = "A" * 129
    mutations.append(certificate)
    email = copy.deepcopy(valid)
    email["email"] = f"{'a' * 245}@test.invalid"
    mutations.append(email)
    for mutation in mutations:
        with pytest.raises(ValidationError):
            validator.validate(mutation)


def test_receipt_bitcoin_metadata_is_exclusive_to_verified_state() -> None:
    validator = Draft202012Validator(load("schemas/timestamp-receipt.schema.json"), format_checker=FormatChecker())
    pending = load("fixtures/timestamp-receipt.pending.valid.json")
    pending["bitcoin"] = {
        "block_height": 1,
        "block_hash": "a" * 64,
        "block_time": "2026-07-30T12:00:00Z",
        "confirmation_policy": "unsafe",
    }
    with pytest.raises(ValidationError):
        validator.validate(pending)
    verified = load("fixtures/timestamp-receipt.verified.valid.json")
    del verified["bitcoin"]
    with pytest.raises(ValidationError):
        validator.validate(verified)


def test_receipt_rejects_proof_larger_than_parser_boundary() -> None:
    validator = Draft202012Validator(load("schemas/timestamp-receipt.schema.json"), format_checker=FormatChecker())
    receipt = load("fixtures/timestamp-receipt.pending.valid.json")
    receipt["proof_bytes"] = 262_145
    with pytest.raises(ValidationError):
        validator.validate(receipt)


def test_status_contract_has_no_email_field_and_rejects_it() -> None:
    schema = load("schemas/order-status.schema.json")
    assert "email" not in schema["properties"]
    status = load("fixtures/order-status.valid.json")
    status["email"] = "leak@example.test"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(status)


@pytest.mark.parametrize(
    "changes",
    [
        {"fulfillment_state": "delivered", "proof_available": True},
        {"fulfillment_state": "awaiting_payment", "calendar_submitted_at": "2026-07-30T12:05:00Z"},
        {"fulfillment_state": "manual_review", "proof_available": True},
        {"fulfillment_state": "queued", "payment_state": "checkout_open"},
    ],
)
def test_status_contract_rejects_impossible_state_projections(changes: dict[str, object]) -> None:
    schema = load("schemas/order-status.schema.json")
    status = load("fixtures/order-status.valid.json")
    status.update(changes)
    if changes.get("fulfillment_state") != "delivered":
        status.pop("bitcoin_verified_at", None)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(status)


def test_status_contract_encodes_customer_visible_artifact_matrix() -> None:
    validator = Draft202012Validator(load("schemas/order-status.schema.json"), format_checker=FormatChecker())
    base = load("fixtures/order-status.valid.json")

    verified_pending_bundle = copy.deepcopy(base)
    verified_pending_bundle.update(
        {
            "fulfillment_state": "bitcoin_verified",
            "bitcoin_verified_at": "2026-07-30T15:10:00Z",
            "proof_available": False,
        }
    )
    validator.validate(verified_pending_bundle)

    stamping = copy.deepcopy(base)
    stamping.update({"fulfillment_state": "stamping", "proof_available": False})
    stamping.pop("calendar_submitted_at")
    validator.validate(stamping)

    delivered_without_bundle = copy.deepcopy(verified_pending_bundle)
    delivered_without_bundle["fulfillment_state"] = "delivered"
    with pytest.raises(ValidationError):
        validator.validate(delivered_without_bundle)


def test_known_manifest_bytes_have_frozen_sha256() -> None:
    manifest = (CONTRACTS / "fixtures/known-manifest.json").read_bytes()
    expected = (CONTRACTS / "fixtures/known-manifest.sha256").read_text(encoding="ascii").split()[0]
    assert manifest.endswith(b"\n")
    assert hashlib.sha256(manifest).hexdigest() == expected
    assert load("fixtures/checkout-request.valid.json")["manifest_sha256"] == expected


def test_openapi_freezes_header_bearer_and_external_schema_references() -> None:
    contract = load("openapi.phase0.json")
    security = contract["components"]["securitySchemes"]["statusToken"]
    assert security == {"type": "http", "scheme": "bearer", "bearerFormat": "vN.base64url-256-bit"}
    assert "{" not in " ".join(contract["paths"])
    for schema in (
        "checkout-request",
        "checkout-response",
        "checkout-price-response",
        "order-status",
        "timestamp-receipt",
        "rotate-token-response",
    ):
        assert (CONTRACTS / f"schemas/{schema}.schema.json").is_file()


def test_openapi_freezes_rotation_and_download_headers() -> None:
    contract = load("openapi.phase0.json")
    rotation = contract["paths"]["/v1/orders/rotate-token"]["post"]["responses"]["200"]
    assert rotation["content"]["application/json"]["schema"]["$ref"] == (
        "./schemas/rotate-token-response.schema.json"
    )
    proof = contract["paths"]["/v1/orders/proof"]["get"]["responses"]["200"]
    assert set(proof["headers"]) == {"Content-Disposition", "Content-Length", "Cache-Control"}
    assert set(proof["content"]) == {"application/zip"}


def test_openapi_freezes_public_checkout_price_contract() -> None:
    contract = load("openapi.phase0.json")
    price = contract["paths"]["/v1/checkout/price"]["get"]
    assert price["security"] == []
    assert price["responses"]["200"]["headers"]["Cache-Control"]["schema"] == {"const": "no-store"}
    assert price["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "./schemas/checkout-price-response.schema.json"
    )
