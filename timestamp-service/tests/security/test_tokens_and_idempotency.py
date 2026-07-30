from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from app.auth.status_token import StoredToken, authenticate_status_token
from app.security.idempotency import bind_idempotency_request
from app.security.tokens import TokenHasher, generate_bearer_token


def test_token_contains_256_random_bits_and_only_hash_is_storable() -> None:
    token = generate_bearer_token(1)
    encoded = token.split(".", 1)[1]
    raw = base64.urlsafe_b64decode(encoded + "=")
    assert len(raw) == 32
    hashed = TokenHasher({1: b"p" * 32}).hash(token)
    assert len(hashed.digest) == 32
    assert token not in repr(hashed)
    assert authenticate_status_token(token, StoredToken(hashed), TokenHasher({1: b"p" * 32}))


def test_rotation_and_revocation_semantics() -> None:
    hasher = TokenHasher({1: b"a" * 32, 2: b"b" * 32})
    old = generate_bearer_token(1)
    replacement = generate_bearer_token(2)
    assert hasher.verify(old, hasher.hash(old))
    assert hasher.verify(replacement, hasher.hash(replacement))
    revoked = StoredToken(hasher.hash(old), revoked_at=datetime.now(UTC))
    assert not authenticate_status_token(old, revoked, hasher)
    assert not hasher.verify(old, hasher.hash(replacement))


def test_verification_uses_constant_time_comparisons(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def recording_compare(left: object, right: object) -> bool:
        nonlocal calls
        calls += 1
        return left == right

    monkeypatch.setattr("app.security.tokens.hmac.compare_digest", recording_compare)
    hasher = TokenHasher({1: b"p" * 32})
    token = generate_bearer_token(1)
    assert hasher.verify(token, hasher.hash(token))
    assert calls == 2


def test_idempotency_requires_128_bits_and_binds_canonical_body() -> None:
    key = base64.urlsafe_b64encode(b"k" * 16).rstrip(b"=").decode("ascii")
    first = bind_idempotency_request(key, "POST", "/v1/checkout", {"b": 2, "a": 1})
    reordered = bind_idempotency_request(key, "POST", "/v1/checkout", {"a": 1, "b": 2})
    changed = bind_idempotency_request(key, "POST", "/v1/checkout", {"a": 2, "b": 2})
    assert first.request_sha256 == reordered.request_sha256
    assert first.request_sha256 != changed.request_sha256
    assert first.key_sha256 == changed.key_sha256
    with pytest.raises(ValueError, match="128"):
        bind_idempotency_request("YWJj", "POST", "/v1/checkout", {})
