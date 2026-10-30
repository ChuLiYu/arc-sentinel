# Tests grow with each feature

[Project map](INDEX.md) · [Registry](components.json) · [Test implementation](../tests)

The existing framework is pytest + pytest-asyncio + respx, with real disposable PostgreSQL for integration tests. Reuse it; TypeScript tests belong with the future TypeScript service when it exists. An empty new framework is not evidence.

| Change | Required evidence |
| --- | --- |
| Pure decoding, validation, rule boundaries | Unit tests for inputs/outputs and boundaries |
| RPC ingestion, transaction scope, schema | PostgreSQL tests asserting rows AND checkpoint before failure, after rollback and after replay |
| API contract/auth | Route tests for accepted and rejected requests |
| Tooling/policy | Temporary-repository tests proving both pass and fail cases |
| Container/deployment | Config validation and build; disclose that this is not a live deployment |

Define acceptance criteria before coding. Add a regression test that fails on the defect when practical; capture the failure and repaired result in the PR. New behavior and its tests ship together. A mechanical refactor can reuse existing tests with a written explanation. Coverage percentages do not replace meaningful assertions.

## Commands

- Fast: `uv run python -m pytest tests/unit tests/tooling -q` (no Docker).
- Focused: `uv run python -m pytest tests/integration/test_indexer_recovery_pg.py -v`.
- Final gate: `./scripts/verify.sh` (mapping/link checks, Ruff, all tests; Docker required).
- Whole PR documentation gate: `python3 scripts/project_check.py --base origin/main`.

Integration fixtures use fixed port 15432/container names; run one suite at a time. Use only disposable fixture databases and mocked RPC; never redirect integration tests to a real database. CI explicitly requires Docker so a missing Docker binary cannot silently turn database coverage into skipped tests. Framework checks live in [tests/tooling](../tests/tooling), separate from runtime tests.

When modifying tests or fixtures, update this document with the changed coverage/fixture contract. No new test is required solely to mirror documentation wording.

## Flow

`natural-language acceptance → smallest useful executable test → focused feedback → full suite with disposable database → failure report`. Tests accompany the feature; they are not postponed to a cleanup phase.

## Constraints and decisions

Reuse pytest/asyncio/respx plus real PostgreSQL fixtures; pure tests stay Docker-free. Workflow contract checks use the standard library rather than adding a YAML dependency. They guard selected configuration expressions, not general YAML correctness. A text scenario defines the requirement; it does not replace executable evidence.

## Pitfalls

A missing Docker daemon must fail the canonical verification and CI, rather than allowing database tests to skip. Parallel full suites can collide on the fixed database port. A green happy path alone cannot establish rollback/replay safety.

## Backlog

Add reorg, multichain identity and restore/load evidence with the corresponding features; see [roadmap](ROADMAP.md). Browser and live-testnet E2E are not established by the current integration suite. Do not add slow end-to-end tests when a focused assertion covers the same invariant reliably.

## Acceptance scenarios

- Given source, agent-policy or registry changes, the temporary Git repository tests prove omitted docs fail, and ordinary prose edits do not recursively demand more docs.
- Given deleted/renamed paths, unmapped/ambiguous ownership, invalid base, independently advanced base, missing knowledge sections or broken/outside links, the tooling suite checks rejection.
- Given CI checkout/base expressions and the persisted reviewer definition, configuration tests check the same revision wiring and parse the agent TOML. A GitHub run still supplies the actual remote execution evidence.

## Knowledge architecture regression evidence

The tooling fixtures also add a previously unknown knowledge document with a broken source link and require rejection, then repair it. Archive classification may exempt historical prose links but cannot hide required component or agent-policy documents. This checks discoverability and linkage, not whether domain prose is true.

## M1 lineage and upgrade evidence

The recovery PostgreSQL fixture includes complete block/transaction/receipt/log identities. It injects parent discontinuity (including inside an empty-wallet batch), forked checkpoints even while caught up, changed final headers, wrong transaction/receipt membership, out-of-range or conflicting duplicate logs, and mid-cycle provider switches. Assertions cover raw rows, journal and checkpoint after failure and clean retry. Catch-up batch caps now run against real PostgreSQL instead of a height-only session mock. Primary/fallback recovery unit tests also require a monotonic provider generation.

The populated-baseline [migration test](../tests/integration/test_migrations_pg.py) downgrades only its disposable database to `0001`, inserts related legacy rows, upgrades to head and checks preservation without invented journal hashes. It checks the new table's model columns and chain-scoped height key. This does not establish backup restore or a live migration.

Five new inconsistency cases were demonstrated failing against the old worker before the fix (parent link, receipt block identity, log range, changed boundary, caught-up fork). Full verification results are recorded once in [VERIFICATION.md](VERIFICATION.md); synthetic data does not establish provider completeness or fork rollback.
