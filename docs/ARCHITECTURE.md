# Architecture decisions and failure semantics

[Project map](INDEX.md)

## ADR 001: retain Python indexing; add TypeScript at the orchestration boundary

The current FastAPI/PostgreSQL indexer already has tested decoding and rule logic. A rewrite would spend effort without proving new reliability properties. The proposed TypeScript service owns future cross-chain event correlation/workflow orchestration; it does not exist yet. Versioned chain-scoped events and an inbox/outbox contract should form the boundary.

## ADR 002: prefer stopped progress to silently missing data

A poll collects its candidate rows before writing. Missing/wrong block responses, missing/invalid receipt status, malformed token logs and invalid log-array responses raise an error. Session cleanup rolls back the transaction; the next cycle starts from the previous checkpoint. A permanently malformed event blocks the pipeline until repaired. A future quarantine mechanism needs persisted provenance, an operator decision and explicit completeness reporting before allowing progress.

This does not prove provider completeness: a provider can return an empty but syntactically valid log array. Block-hash consistency is now checked, but independent provider reconciliation and reference-range comparisons remain next-stage work.

## ADR 003: transaction-scoped writer coordination

Every worker attempts `pg_try_advisory_xact_lock` before reading its checkpoint. The bigint key combines an application namespace with the validated chain ID. Failure defers the poll without writes. PostgreSQL releases the lock on commit/rollback or connection loss; no wall-clock lease expiry is needed.

The lock is advisory: direct/manual writers can bypass it. A connection and transaction stay open during RPC calls, increasing pool usage and transaction lifetime. This is acceptable for the single-chain pilot; keep concurrency bounded and measure before replacing it with a more complicated lease protocol. Test with two real PostgreSQL sessions, not a mock lock.

Lock scope is per chain/database, but the current ledger schema is still single-chain. Locks do not make that schema safe for multichain use.

## ADR 004: explicit history origin

On a new database with no `START_BLOCK`, record a hash anchor at `head - confirmations` without indexing older activity. For explicit starts above genesis, journal the preceding block as an anchor. Anchors do not assert historical wallet coverage. With a configured start, index inclusively in bounded batches; a future start writes no checkpoint. Existing checkpoints always take precedence. A pre-journal checkpoint cannot safely acquire its historical hash from today's provider: revision `0002` preserves it, and the worker refuses to resume it. Preserve the original database and use a separately reviewed rebuild/reconciliation; do not seed an assumed hash. Registering a wallet later does not imply historical backfill. Operator rescan must respect single-writer coordination and explain the resulting duplicate-write behavior.

## ADR 005: separate tests, build, publication and deployment

PRs run tests and build an image without registry publication. Main builds publish a full-commit-SHA tag with a lowercase repository name. Deployments require manual dispatch, the main branch and an explicit `DEPLOY_ENABLED` variable; host secrets are provisioned outside Git and never rendered through CI shell interpolation. SSH uses a pinned known-hosts entry.

An image tag identifies the intended revision but can be overwritten by a sufficiently privileged publisher; use a registry digest when a promotion system is added. A successful image build is not evidence of a successful cloud deployment. [GitHub documents the restriction on secrets in conditions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

## ADR 006: reject inconsistent views before any batch writes

Adopted 2026-10-30 as the first M1 slice. The journal and checkpoint commit atomically with raw rows. Each cycle rechecks the saved checkpoint hash, including when caught up; parent hashes connect fetched blocks to that checkpoint (or explicit-start predecessor). Transactions, receipts and token logs must agree with fetched block identities and ranges. The final header is reread before writes. These checks detect inconsistency, not consensus finality or provider omission.

Transparent provider switching during a batch was rejected: a preflight chain-ID check cannot protect later calls. A provider generation counter rejects a cycle that changes providers, even if it returns to primary before validation. Rechecking chain ID and boundary at the end complements the counter. The next clean cycle may commit from a stable fallback. This costs extra header/chain calls and retries, and empty-wallet ranges also fetch headers to preserve lineage. Measure RPC cost before tuning away checks.

Automatic rollback is deferred until raw and derived-data repair has a tested recovery protocol. Bounded ancestor search and durable recovery latching extend this decision below. See [M1 remaining gates](ROADMAP.md) and the [operator procedure](RUNBOOK.md).

## ADR 007: diagnose forks and latch recovery before repair

Adopted 2026-10-30. A mismatched checkpoint searches backward only through a contiguous provider branch and known journal provenance, bounded by `REORG_MAX_DEPTH`. Rechecking the checkpoint header, chain and provider generation rejects mixed views before persisting a reason, optional ancestor and detection time under the writer lock. Legacy height-only state is also latched, without fabricating hashes. Transient input/view errors remain retryable.

This status transaction intentionally commits no ledger, journal or height change. A durable latch survives worker restart or return to the old provider branch; otherwise near-head lag could look healthy despite a fork. Health therefore reports `degraded` for a latch, including during RPC failure. Automatic latch clearing was rejected until canonical replay and derived-data repair are implemented. There is no reset endpoint. Operators preserve the original database and use the reviewed separate-database rebuild procedure; a found ancestor is diagnostic, not permission to delete rows. Existing ledger reads still expose the stored pre-repair view.

## Remaining correctness boundaries

A hash journal, bounded fork diagnosis and persisted recovery stop now exist; automatic reorg repair does not. Other retryable indexing failures may still appear only in logs, and a null latch does not establish freshness. A transaction has one agent projection, rules execute in API memory, read routes are public, and native-gas valuation is Arc-specific. These are tracked as launch gates in the roadmap, rather than hidden behind a “production-ready” label.
