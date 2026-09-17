"""API dependencies: X-API-Key auth (SPEC §7/§8, Phase 4) and session getter.

Write-scope endpoints verify the ``X-API-Key`` header against the ``api_keys``
table (SPEC §17): the stored ``key_hash`` is ``sha256(salt + key)`` where
``salt`` comes from ``SENTINEL_API_KEY_SALT``. When the salt is not configured
the legacy ``API_KEY_HASH`` env comparison is kept as a fallback so existing
deployments keep working.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiKey
from app.db.session import get_session


def _hash_api_key(key: str, salt: str = "") -> str:
    """Hash an API key for storage/comparison (SPEC §17: sha256(salt + key))."""
    return hashlib.sha256((salt + key).encode("utf-8")).hexdigest()


def require_api_key(
    request: Request, session: Session = Depends(get_session)
) -> None:
    """FastAPI dependency verifying the write-scope ``X-API-Key`` header.

    Resolution order (SPEC §17):

    1. If ``SENTINEL_API_KEY_SALT`` is configured, look the key up in the
       ``api_keys`` table (``sha256(salt + key)``, active rows only).
    2. Otherwise fall back to the legacy ``API_KEY_HASH`` env comparison.
    3. When neither is configured the write endpoints are disabled (503).
    """
    settings = get_settings()

    key = request.headers.get("X-API-Key", "")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Key header",
        )

    if not settings.api_key_salt and not settings.api_key_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="write endpoints disabled (SENTINEL_API_KEY_SALT / API_KEY_HASH not configured)",
        )

    if settings.api_key_salt:
        candidate = _hash_api_key(key, settings.api_key_salt)
        row = session.scalar(
            select(ApiKey).where(
                ApiKey.key_hash == candidate,
                ApiKey.is_active.is_(True),
            )
        )
        if row is not None:
            return
        # not found → invalid key below (shared 403 path)
    else:
        candidate = _hash_api_key(key)
        # hmac.compare_digest avoids timing attacks on the hash comparison.
        if hmac.compare_digest(candidate, settings.api_key_hash):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="invalid API key",
    )
