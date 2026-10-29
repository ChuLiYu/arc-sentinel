# Database component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

SQLAlchemy models, sessions and Alembic migrations. Current keys are single-chain; migrate before multichain. Preserve FK ordering and checkpoint atomicity. Test migrations on PostgreSQL, not SQLite.

## Entry points

- [app/db](../../app/db)

## Verification

- [tests/integration/test_worker_pg.py](../../tests/integration/test_worker_pg.py)

## Flow

`Alembic migrations → session factory → domain rows/upserts → indexer state → one commit`. On exception, session closure rolls back the open transaction. Migration files are part of the deployment artifact.

## Constraints and decisions

PostgreSQL is required by ON CONFLICT/advisory-lock semantics; SQLite cannot validate those guarantees. SQLAlchemy models and Alembic migrations must evolve together. Current transaction identities are single-chain. Do not reuse the current schema for another chain without migration and collision tests. Original selection dates are unknown.

## Pitfalls

Transfer rows depend on transaction rows: preserve insert order and referential integrity. A successful schema import does not prove an existing database can migrate or roll back. Never point disposable fixtures at production data.

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): chain-scoped keys, migration upgrade validation and backup/restore drill. Acceptance requires preserving existing data through an upgrade and measuring a restore; no such production recovery measurement is claimed.

## Acceptance scenarios

- Given a disposable PostgreSQL database, when the worker ingests the same range twice, then unique identities prevent duplicates. Evidence: `test_poll_cycle_indexes_then_stays_idempotent` below.
- Given a mid-write exception, then transactions/transfers/checkpoint revert together. Evidence: [recovery tests](../../tests/integration/test_indexer_recovery_pg.py).
- Existing-database migration/backup-restore E2E remains unimplemented; fixture schema creation is not equivalent evidence.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
