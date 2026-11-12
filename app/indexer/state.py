"""Read/write of ``indexer_state`` — the breakpoint table (SPEC §4 / §7).

The worker persists ``last_indexed_block`` in the *same transaction* that
writes the indexed rows, so a restart always resumes from a consistent
breakpoint and the ``tx_hash`` UNIQUE constraints make re-indexing idempotent.
"""

from __future__ import annotations

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import IndexerState

__all__ = ["get_last_indexed_block", "set_last_indexed_block", "try_indexer_lock"]


def try_indexer_lock(session: Session, chain_id: int) -> bool:
    """Nonblocking, transaction-scoped single-writer lock per chain/database.

    PostgreSQL releases it on commit, rollback or connection loss. All writers
    must use this protocol; it does not protect against manual checkpoint edits.
    The upper bits namespace these locks away from other application locks.
    """
    key = (0x415243 << 32) | chain_id
    return bool(session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}))


def get_last_indexed_block(session: Session, chain_id: int) -> int | None:
    """Return the last indexed block for *chain_id* (None on first run)."""
    row = session.get(IndexerState, chain_id)
    return row.last_indexed_block if row is not None else None


def _upsert_stmt(chain_id: int, block: int):
    """PostgreSQL upsert statement for ``indexer_state``."""
    return (
        pg_insert(IndexerState)
        .values(chain_id=chain_id, last_indexed_block=block)
        .on_conflict_do_update(
            index_elements=["chain_id"],
            set_={
                "last_indexed_block": block,
                "updated_at": func.now(),
            },
        )
    )


def set_last_indexed_block(session: Session, chain_id: int, block: int) -> None:
    """Upsert ``last_indexed_block`` (no commit — caller owns the transaction)."""
    session.execute(_upsert_stmt(chain_id, block))
