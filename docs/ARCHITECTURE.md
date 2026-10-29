# Architecture decisions and failure semantics

[Project map](INDEX.md)

## ADR 001: retain Python indexing; add TypeScript at the orchestration boundary

The current FastAPI/PostgreSQL indexer already has tested decoding and rule logic. A rewrite would spend effort without proving new reliability properties. The proposed TypeScript service owns future cross-chain event correlation/workflow orchestration; it does not exist yet. Versioned chain-scoped events and an inbox/outbox contract should form the boundary.

## ADR 002: prefer stopped progress to silently missing data

A poll collects its candidate rows before writing. Missing/wrong block responses, missing/invalid receipt status, malformed token logs and invalid log-array responses raise an error. Session cleanup rolls back the transaction; the next cycle starts from the previous checkpoint. A permanently malformed event blocks the pipeline until repaired. A future quarantine mechanism needs persisted provenance, an operator decision and explicit completeness reporting before allowing progress.

This does not prove provider completeness: a provider can return an empty but syntactically valid log array. Cross-provider reconciliation, block-hash consistency and reference-range comparisons are next-stage work.

## ADR 003: transaction-scoped writer coordination

Every worker attempts `pg_try_advisory_xact_lock` before reading its checkpoint. The bigint key combines an application namespace with the validated chain ID. Failure defers the poll without writes. PostgreSQL releases the lock on commit/rollback or connection loss; no wall-clock lease expiry is needed.

The lock is advisory: direct/manual writers can bypass it. A connection and transaction stay open during RPC calls, increasing pool usage and transaction lifetime. This is acceptable for the single-chain pilot; keep concurrency bounded and measure before replacing it with a more complicated lease protocol. Test with two real PostgreSQL sessions, not a mock lock.

Lock scope is per chain/database, but the current ledger schema is still single-chain. Locks do not make that schema safe for multichain use.

## ADR 004: explicit history origin

On a new database with no `START_BLOCK`, anchor at `head - confirmations` without indexing older activity. With a configured start, index inclusively in bounded batches; a future start writes no checkpoint. Existing checkpoints always take precedence. Registering a wallet later does not imply historical backfill. Operator rescan must respect single-writer coordination and explain the resulting duplicate-write behavior.

## ADR 005: separate tests, build, publication and deployment

PRs run tests and build an image without registry publication. Main builds publish a full-commit-SHA tag with a lowercase repository name. Deployments require manual dispatch, the main branch and an explicit `DEPLOY_ENABLED` variable; host secrets are provisioned outside Git and never rendered through CI shell interpolation. SSH uses a pinned known-hosts entry.

An image tag identifies the intended revision but can be overwritten by a sufficiently privileged publisher; use a registry digest when a promotion system is added. A successful image build is not evidence of a successful cloud deployment. [GitHub documents the restriction on secrets in conditions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

## Remaining correctness boundaries

The current checkpoint is height-only. No hash journal/reorg repair exists, and polling multiple providers can mix chain views. A transaction has one agent projection, rules execute in API memory, read routes are public, and native-gas valuation is Arc-specific. These are tracked as launch gates in the roadmap, rather than hidden behind a “production-ready” label.
