#!/usr/bin/env python3
"""Generate a write-scope API key and store its hash in the ``api_keys`` table.

Usage:
    .venv/bin/python scripts/gen_api_key.py [--label <label>] [--db URL]

The printed plaintext key is shown ONCE — save it in your password manager.
Only the sha256(salt + key) hash is stored (SPEC §17).

Requires ``SENTINEL_API_KEY_SALT`` in the environment/.env, otherwise the
script refuses to run (a salt-less hash would silently disable the key).
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import _hash_api_key
from app.config import Settings
from app.db.models import ApiKey


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="write-scope", help="label for the key")
    parser.add_argument(
        "--db",
        default=None,
        help="DATABASE_URL override (defaults to settings.database_url)",
    )
    args = parser.parse_args()

    settings = Settings()
    if not settings.api_key_salt:
        print(
            "ERROR: SENTINEL_API_KEY_SALT is not configured.\n"
            "Set it (e.g. `openssl rand -hex 32`) before issuing API keys.",
            file=sys.stderr,
        )
        return 1

    url = args.db or settings.database_url
    raw_key = secrets.token_urlsafe(32)
    key_hash = _hash_api_key(raw_key, settings.api_key_salt)

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            session.add(ApiKey(key_hash=key_hash, label=args.label))
            session.commit()
    finally:
        engine.dispose()

    print("API key created (hashed with SENTINEL_API_KEY_SALT).")
    print(f"label : {args.label}")
    print(f"key    : {raw_key}")
    print("\nSave the key above now — it is shown only once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
