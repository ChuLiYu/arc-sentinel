# Rules component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

Budget, recipient and anomaly evaluation plus persistence. The API owns an in-process runner; no durable/exactly-once notification guarantee. Test rule boundaries and repeat evaluation.

## Entry points

- [app/rules](../../app/rules)

## Verification

- [tests/unit/test_rules.py](../../tests/unit/test_rules.py)
- [tests/unit/test_rules_runner.py](../../tests/unit/test_rules_runner.py)
- [tests/integration/test_rules_runner_pg.py](../../tests/integration/test_rules_runner_pg.py)

## Flow

`runner tick → active agents + recent ledger rows → budget/recipient/anomaly evaluation → alert persistence`. The API process owns the periodic task through application lifecycle; this is not a durable external queue.

## Constraints and decisions

Pure rule evaluation remains separate from the database runner so boundary logic can be tested cheaply. Preserve exact monetary arithmetic and explicit time windows. SQL-backed alert persistence is current behavior, not proof of exactly-once external delivery. Original selection dates are unknown.

## Pitfalls

Restarting the API interrupts its in-process task. Re-evaluation and empty windows need tests; duplicate or reordered upstream events can affect derived totals. Do not add external notifications without defining retries, deduplication and delivery semantics.

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): reconcile alerts after reorgs and define durable notification delivery if added. Acceptance must include crash/replay and deduplication, not only a successful single evaluation.

## Acceptance scenarios

- Given a spending threshold and a defined window, when totals are below/at/above the boundary, then the budget decision follows the configured rule. Evidence: pure cases in test_rules.py below.
- Given stored ledger data, when a runner evaluates it, then the expected alert is persisted. Evidence: test_rules_runner_pg.py below.
- External notification delivery and crash-recovery E2E are not implemented guarantees.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
