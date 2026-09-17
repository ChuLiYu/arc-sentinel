"""API dependencies: X-API-Key auth (SPEC §8) and shared session getter."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request, status

from app.config import get_settings


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def require_api_key(request: Request) -> None:
    """FastAPI dependency verifying the write-scope ``X-API-Key`` header.

    The configured value is stored as a sha256 hash (``API_KEY_HASH`` in env,
    SPEC §7 api_keys). An empty configured hash disables write endpoints.
    """
    settings = get_settings()
    if not settings.api_key_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write endpoints disabled (API_KEY_HASH not configured)",
        )

    key = request.headers.get("X-API-Key", "")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Key header",
        )

    candidate = _hash_api_key(key)
    # hmac.compare_digest avoids timing attacks on the hash comparison.
    if not hmac.compare_digest(candidate, settings.api_key_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid API key",
        )
