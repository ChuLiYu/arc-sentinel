# Indexer component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

Block ingestion, decoding, RPC retries, writer lock and checkpoint. Preserve atomic rows/checkpoint commits; incomplete inputs must stop progress. Reorg recovery is still missing.

## Entry points

- [app/indexer](../../app/indexer)

## Verification

- [tests/unit/test_worker.py](../../tests/unit/test_worker.py)
- [tests/unit/test_rpc_client.py](../../tests/unit/test_rpc_client.py)
- [tests/integration/test_indexer_recovery_pg.py](../../tests/integration/test_indexer_recovery_pg.py)

## Flow

`chain ID + head → safe head → transaction writer lock → checkpoint/start → bounded range → blocks/receipts/logs → rows + checkpoint commit`.
Wrong chain fails before writes; competing writer defers. Any incomplete input or write error rolls back the batch. No START_BLOCK on first run initializes at safe head; explicit START_BLOCK is inclusive. No active wallets still advances the checkpoint, so newly registered wallets do not get automatic historical backfill.

## Constraints and decisions

Current implementation uses bounded HTTP polling and PostgreSQL upserts/transaction-scoped advisory lock. Polling limits work per cycle; unique keys make replay idempotent; rows and checkpoint share a commit. Do not replace atomicity with a separate checkpoint write. This records the observed design; older tradeoff dates are unknown. Confirmations reduce exposure but do not implement canonical-chain reconciliation.

## Pitfalls

The 2026-10-29 recovery fix rejects missing blocks, receipts and undecodable logs rather than skipping them and losing events. Lock release on rollback is tested. Do not treat RPC failure as an empty successful batch, or infer multichain safety from chain-ID validation.

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): add block-hash lineage/reorg rollback and derived-data repair; accept only when a fork/replay scenario leaves canonical rows and checkpoint consistent. Backfill for newly registered wallets needs a separate explicit product/retention policy.

## Acceptance scenarios

- Given an established checkpoint, when a batch has a missing receipt, then rows and checkpoint stay unchanged; repairing RPC and replaying writes each event once. Evidence: `test_incomplete_batch_retries_without_losing_events` in the recovery suite below.
- Given inserted rows in an uncommitted batch, when a database write fails, then all rows/checkpoint roll back. Evidence: `test_database_failure_rolls_back_rows_and_checkpoint`.
- Given two writers on one chain, when the second attempts a cycle, then it cannot write; rollback releases ownership. Evidence: `test_competing_worker_cannot_write_and_lock_releases_on_rollback`.
- Real RPC/testnet multi-day operation and reorg E2E evidence are still missing; mocked RPC + real PostgreSQL are integration evidence.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
