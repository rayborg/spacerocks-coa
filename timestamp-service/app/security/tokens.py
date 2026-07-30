from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

_TOKEN = re.compile(r"^v([1-9][0-9]*)\.([A-Za-z0-9_-]{43})$")
_TOKEN_CONTEXT = b"spacerocks-status-token\x00"


@dataclass(frozen=True, slots=True)
class HashedToken:
    version: int
    digest: bytes

    def __post_init__(self) -> None:
        if self.version < 1 or len(self.digest) != 32:
            raise ValueError("invalid versioned token hash")


def generate_bearer_token(version: int) -> str:
    if version < 1:
        raise ValueError("token version must be positive")
    raw = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    return f"v{version}.{raw}"


class TokenHasher:
    def __init__(self, peppers: Mapping[int, bytes]) -> None:
        normalized = {version: bytes(pepper) for version, pepper in peppers.items()}
        if not normalized or any(version < 1 or len(pepper) < 32 for version, pepper in normalized.items()):
            raise ValueError("each token pepper must be versioned and contain at least 32 bytes")
        self._peppers = normalized

    def hash(self, token: str) -> HashedToken:
        match = _TOKEN.fullmatch(token)
        if match is None:
            raise ValueError("invalid bearer token format")
        version = int(match.group(1))
        pepper = self._peppers.get(version)
        if pepper is None:
            raise ValueError("unknown token pepper version")
        digest = hmac.new(pepper, _TOKEN_CONTEXT + token.encode("ascii"), hashlib.sha256).digest()
        return HashedToken(version=version, digest=digest)

    def verify(self, token: str, expected: HashedToken) -> bool:
        try:
            candidate = self.hash(token)
        except ValueError:
            candidate = HashedToken(expected.version, b"\x00" * 32)
        version_matches = hmac.compare_digest(candidate.version.to_bytes(8, "big"), expected.version.to_bytes(8, "big"))
        digest_matches = hmac.compare_digest(candidate.digest, expected.digest)
        return version_matches and digest_matches
