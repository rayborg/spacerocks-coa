from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.security.tokens import HashedToken, TokenHasher


@dataclass(frozen=True, slots=True)
class StoredToken:
    token_hash: HashedToken
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


def authenticate_status_token(
    raw_token: str,
    stored: StoredToken,
    hasher: TokenHasher,
    now: datetime | None = None,
) -> bool:
    valid = hasher.verify(raw_token, stored.token_hash)
    unexpired = stored.expires_at is None or (now is not None and stored.expires_at > now)
    return valid and stored.revoked_at is None and unexpired
