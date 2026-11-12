# Indexer component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

Block ingestion, validated lineage, RPC retries, writer lock and checkpoint. Preserve atomic rows/journal/checkpoint commits; incomplete or inconsistent inputs stop progress. Automatic reorg recovery is still missing.

## Entry points

- [app/indexer](../../app/indexer)

## Verification

- [tests/unit/test_worker.py](../../tests/unit/test_worker.py)
- [tests/unit/test_rpc_client.py](../../tests/unit/test_rpc_client.py)
- [tests/integration/test_indexer_recovery_pg.py](../../tests/integration/test_indexer_recovery_pg.py)

## Flow

`chain ID + provider generation → safe head → transaction writer lock → recovery latch + checkpoint hash → bounded contiguous blocks → bound transactions/receipts/logs → boundary + provider recheck → rows + journal + checkpoint commit`.
Wrong chain fails before writes; competing writer defers. Any incomplete input, inconsistent lineage or write error rolls back the batch. The saved checkpoint hash is checked even when caught up. No START_BLOCK on first run records a safe-head anchor without indexing older history; explicit START_BLOCK is inclusive and records its predecessor as an anchor when above genesis. No active wallets still validates and journals blocks, then advances the checkpoint, so newly registered wallets do not get automatic historical backfill.

## Constraints and decisions

Current implementation uses bounded HTTP polling and PostgreSQL upserts/transaction-scoped advisory lock. Polling limits work per cycle; unique keys make replay idempotent; rows, block journal and checkpoint share a commit. Do not replace atomicity with a separate checkpoint write. Confirmations reduce exposure but do not implement canonical-chain reconciliation.

The M1 foundation adopted on 2026-10-30 binds transaction and receipt identities and token logs to fetched block hashes/ranges, checks parent continuity and rereads the batch boundary before writes. A monotonically increasing provider generation rejects any provider switch during the cycle, including a switch back to primary; a clean next cycle may use the fallback. This favors retry over mixing views. It does not independently prove that a provider returned every transaction/log. See [architecture decisions](../../docs/ARCHITECTURE.md).

A checkpoint hash mismatch searches at most `REORG_MAX_DEPTH` preceding headers (default 64), including an ancestor exactly at the limit. Every fetched parent must link to its child; the checkpoint header, chain ID and provider generation are rechecked before committing diagnostics. A match records `checkpoint_fork` and the common ancestor. A missing journal entry, mismatching origin anchor/genesis or exhausted window records `journal_gap`, `history_boundary` or `rollback_window_exceeded` without inventing an ancestor. Raw rows, journal and height remain untouched. This diagnostic search does not enable rollback.

## Pitfalls

The 2026-10-29 recovery fix rejects missing blocks, receipts and undecodable logs rather than skipping them and losing events. Lock release on rollback is tested. Do not treat RPC failure as an empty successful batch, or infer multichain safety from chain-ID validation. A legacy checkpoint without a journal entry latches `legacy_checkpoint`; changing START_BLOCK does not repair it. Validated recovery diagnostics persist under the writer lock before the worker raises; restart or a provider returning to the old branch cannot clear them. API health reports the latch even if RPC is down or lag is zero. Transient missing/malformed headers, inconsistent search ancestry or provider/chain changes roll back without latching. Preserve the database and follow the [runbook](../../docs/RUNBOOK.md).

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): add reorg rollback and derived-data repair; accept only when a fork/replay scenario leaves canonical rows and checkpoint consistent. Recovery latches have no automatic reset or in-place repair command. Backfill for newly registered wallets needs a separate explicit product/retention policy.

## Acceptance scenarios

- Given an established checkpoint, when a batch has a missing receipt, then rows and checkpoint stay unchanged; repairing RPC and replaying writes each event once. Evidence: `test_incomplete_batch_retries_without_losing_events` in the recovery suite below.
- Given inserted rows in an uncommitted batch, when a database write fails, then all rows/checkpoint roll back. Evidence: `test_database_failure_rolls_back_rows_and_checkpoint`.
- Given two writers on one chain, when the second attempts a cycle, then it cannot write; rollback releases ownership. Evidence: `test_competing_worker_cannot_write_and_lock_releases_on_rollback`.
- Given a parent/hash/member/range mismatch, changed batch boundary or provider switch, then the recovery suite requires rows, journal and checkpoint to stay unchanged; a clean fallback cycle may retry successfully.
- Given a forked checkpoint while caught up, or an old height-only checkpoint, then polling stops without inventing lineage or advancing state. Even an empty-wallet range must have contiguous validated headers.
- Given 1/3/10-block forks within the configured window, then the common ancestor and detection time persist across a new worker instance. A deeper fork stops at the configured bound, and origin/gap cases have no invented ancestor. Invalid or changing RPC views cannot persist a recovery decision. Evidence: fork-search cases in the recovery suite.
- Real RPC/testnet multi-day operation and reorg E2E evidence are still missing; mocked RPC + real PostgreSQL are integration evidence.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
