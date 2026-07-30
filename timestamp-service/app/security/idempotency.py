from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_KEY = re.compile(r"^[A-Za-z0-9_-]{22,86}$")


@dataclass(frozen=True, slots=True)
class IdempotencyBinding:
    key_sha256: bytes
    request_sha256: bytes
    canonical_body: bytes


def _decode_key(key: str) -> bytes:
    if _KEY.fullmatch(key) is None:
        raise ValueError("idempotency key must be unpadded base64url containing 128 through 512 bits")
    try:
        decoded = base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise ValueError("idempotency key must be valid base64url") from error
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != key or not 16 <= len(decoded) <= 64:
        raise ValueError("idempotency key must contain 128 through 512 bits")
    return decoded


def canonical_json(body: Any) -> bytes:
    try:
        serialized = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return serialized.encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("request body is not canonicalizable JSON") from error


def bind_idempotency_request(key: str, method: str, path: str, body: Any) -> IdempotencyBinding:
    key_bytes = _decode_key(key)
    if not method.isascii() or method.upper() != method or not path.startswith("/"):
        raise ValueError("method and path must be canonical")
    canonical_body = canonical_json(body)
    request = b"\x00".join((method.encode("ascii"), path.encode("utf-8"), canonical_body))
    return IdempotencyBinding(
        key_sha256=hashlib.sha256(b"spacerocks-idempotency-key\x00" + key_bytes).digest(),
        request_sha256=hashlib.sha256(request).digest(),
        canonical_body=canonical_body,
    )
