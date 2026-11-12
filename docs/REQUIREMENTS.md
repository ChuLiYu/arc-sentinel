# Requirement status and evidence

[Project map](INDEX.md) · [Contract sources](CONTRACTS.md) · [Milestone acceptance](ROADMAP.md)

The roadmap owns product direction and milestone exit gates. This trace index connects those gates and observed baseline behavior to components and evidence without promoting implementation details into approved requirements. Historical approval records for individual prototype rules are unavailable; those rules remain observed behavior until intentionally changed. Text scenarios in component documents own local Given/When/Then detail.

| ID | Intent / origin | Status and scenario owner | Evidence or remaining gap |
| --- | --- | --- | --- |
| ING-ATOMIC | M0: incomplete ingestion must not lose events | Implemented; [indexer](../app/indexer/README.md) | [Recovery suite](../tests/integration/test_indexer_recovery_pg.py): missing input, failed write and retry |
| ING-WRITER | M0: one writer per chain/database | Implemented; [indexer](../app/indexer/README.md) | Same suite: competing advisory lock and rollback release |
| ING-START | M0: inclusive operator backfill start | Implemented; [indexer](../app/indexer/README.md) | Same suite: genesis, ordinary and future starts; no automatic per-wallet backfill |
| CHAIN-CANONICAL | M1: canonical ledger survives fork/replay | In progress; [roadmap M1](ROADMAP.md) owns the complete gate | Journal, identity/parent/boundary checks, bounded ancestor search and persistent recovery stop tested in the [recovery suite](../tests/integration/test_indexer_recovery_pg.py); rollback, derived-data repair and clean-replay equality remain |
| WALLET-LEDGER | M1: raw events separated from wallet attribution | Planned; [database](../app/db/README.md) | Current single agent/direction projection lacks both-wallet, self-transfer and relayer ledger evidence |
| RULE-BASELINE | Prototype budget, anomaly and recipient rules | Observed/tested, historical approval unknown; [rules](../app/rules/README.md) | [Rule tests](../tests/unit/test_rules.py); no new financial-policy approval inferred |
| API-BASELINE | Prototype authenticated operator API/dashboard | Observed/tested, historical approval unknown; [API](../app/api/README.md), [dashboard](../app/dashboard/README.md) | [API tests](../tests/integration/test_api.py); not evidence of public/multi-tenant readiness |
| MSG-LIFECYCLE | M2: durable two-chain message observer | Planned; [roadmap M2](ROADMAP.md) | No TypeScript observer, selected pathway or workflow recovery evidence yet |
| OPS-PILOT | M3: bounded-cost, recoverable testnet operation | Planned; [runbook](RUNBOOK.md) | No live pilot, restore measurement or seven-day observation yet |
| RELEASE-PUBLIC | M4: reproducible public portfolio release | Planned; [roadmap M4](ROADMAP.md) | Source published with the 2026-11-01 security review ([SECURITY.md](../SECURITY.md)); M4 reproducibility gates not yet met |

Changing a rule requires updating its intent/status here only when that status changes, its owning scenario and implementation/tests together. Verification results live in [VERIFICATION.md](VERIFICATION.md); this index does not duplicate run counts. A green test run does not close a milestone with missing scenarios.
