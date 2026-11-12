# Database component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

SQLAlchemy models, sessions and Alembic migrations. Raw transaction/transfer keys remain single-chain; the new block journal is chain-scoped. Migrate raw identities before multichain. Preserve FK ordering and checkpoint atomicity. Test migrations on PostgreSQL, not SQLite.

## Entry points

- [app/db](../../app/db)

## Verification

- [tests/integration/test_worker_pg.py](../../tests/integration/test_worker_pg.py)
- [tests/integration/test_migrations_pg.py](../../tests/integration/test_migrations_pg.py)

## Flow

`ordered Alembic migrations → session factory → domain rows/upserts + block journal → indexer state → one commit`. On exception, session closure rolls back the open transaction. Migration files are part of the deployment artifact.

## Constraints and decisions

PostgreSQL is required by ON CONFLICT/advisory-lock semantics; SQLite cannot validate those guarantees. Models define the target schema; ordered migrations own upgrade history. Add revisions rather than editing applied migrations. Revision `0002` adds `indexed_blocks`, keyed by chain/height with hash, parent, time, logical provider and anchor provenance. Anchors mark history boundaries, not fully indexed historical activity. Existing rows/checkpoints are preserved, and no legacy hashes are synthesized. Current transaction identities are single-chain. Do not reuse the current schema for another chain without migration and collision tests. Original selection dates are unknown.

Revision `0003` adds nullable recovery reason, common-ancestor height and detection time to `indexer_state`. Upgrade preserves rows and leaves these fields null; it does not evaluate old checkpoints. The worker latches diagnostics in a separate no-ingestion transaction under its existing advisory lock. Once present, they prevent ingestion without modifying raw rows, the journal or checkpoint. The migration test also downgrades/upgrades `0003` on populated state; downgrade removes diagnostics, so it is not a recovery reset procedure and an older worker cannot enforce the latch.

## Pitfalls

Transfer rows depend on transaction rows: preserve insert order and referential integrity. A successful schema import does not prove an existing database can migrate or roll back. Never point disposable fixtures at production data. Schema upgrade succeeds for height-only checkpoints, but the worker refuses to resume them without known hash provenance; see the [runbook](../../docs/RUNBOOK.md). Downgrading `0002` deletes the journal and therefore removes evidence needed by the new worker; it is not a reorg recovery procedure.

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): chain-scoped raw identities, wallet ledger, automated fork recovery and backup/restore drill. Populated-baseline upgrade preservation is now tested; no production restore measurement is claimed.

## Acceptance scenarios

- Given a disposable PostgreSQL database, when the worker ingests the same range twice, then unique identities prevent duplicates. Evidence: `test_poll_cycle_indexes_then_stays_idempotent` below.
- Given a mid-write exception, then transactions/transfers/checkpoint revert together. Evidence: [recovery tests](../../tests/integration/test_indexer_recovery_pg.py).
- Given populated revision `0001`, when upgraded to head, then existing agents, transactions, transfers and height checkpoint survive unchanged, the journal remains empty, and the new table permits equal heights on different chains. Evidence: migration integration test above.
- Given populated revision `0003`, when downgraded to `0002` then upgraded again in a disposable database, checkpoint and journal survive while removed diagnostics return null. Evidence: the independent recovery-revision migration test above; this is schema behavior, not permission to downgrade a running indexer.
- Live-database migration and backup/restore E2E remain unmeasured; the disposable populated-baseline test is not operational recovery evidence.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
