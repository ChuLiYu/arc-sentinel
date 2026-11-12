# Contract authority

[Project map](INDEX.md) · [Requirements and evidence](REQUIREMENTS.md)

These are code-first contract sources, not parallel schema definitions. Change the source below, synchronize its component knowledge and scenarios, then run the indicated checks. A test demonstrates an implementation; it cannot approve a product requirement.

| Contract / maintainer component | Authority | Update and dependent knowledge | Validation |
| --- | --- | --- | --- |
| HTTP shapes / API | Pydantic models alongside [agent routes](../app/api/routes_agents.py), [transactions](../app/api/routes_transactions.py), [alerts](../app/api/routes_alerts.py), [stats](../app/api/routes_stats.py) and [dependencies](../app/api/deps.py) | Route, validation, authorization or response changes update [API](../app/api/README.md); dashboard consumes responses | API integration suite in [testing](TESTING.md) |
| Health response / runtime | `/health` in [application entrypoint](../app/main.py) | Lag and recovery-state fields update [runtime](components/RUNTIME.md), [API](../app/api/README.md) and [runbook](RUNBOOK.md); HTTP 200 consumers must inspect the status body | Health cases in the API integration suite |
| Storage / database | [Models](../app/db/models.py) for target schema; ordered [migrations](../app/db/migrations/versions) for upgrades | Add a migration rather than rewriting a published revision; synchronize [database](../app/db/README.md) and affected producers/consumers | Real PostgreSQL migration and worker tests |
| Ingestion / indexer | [Worker](../app/indexer/worker.py), [state protocol](../app/indexer/state.py), [RPC](../app/indexer/rpc_client.py) | Ingestion and checkpoint changes update [indexer](../app/indexer/README.md); storage changes also follow database authority | Worker unit and PostgreSQL recovery suites |
| Rule evaluation / rules | [Rules package](../app/rules) | Rule semantics and outputs update [rules](../app/rules/README.md); intended changes require acceptance, not just snapshots of code | Rules unit and PostgreSQL runner suites |
| Configuration / runtime | [Settings](../app/config.py), [packaging](../pyproject.toml), [example environment](../.env.example) | Settings/defaults update [runtime](components/RUNTIME.md), example and affected runbook commands; lockfile follows package tooling | Config tests, frozen install and container smoke |
| Change gates / automation | [Registry](components.json), [checker](../scripts/project_check.py), [workflow](../.github/workflows/ci.yml) | Policy changes update [workflow knowledge](WORKFLOW.md) and fixture expectations together | Isolated Git policy fixtures and workflow contract tests |

Generated OpenAPI is derived from the API source at runtime; do not hand-maintain another schema. No checked-in SDK or generated API document currently exists. Introducing one requires a reproducible generator and freshness gate. Alembic revisions are versioned upgrade instructions, not generated documentation. No generic contract generator is added to this Python project.

Amounts and precision retain the existing model/rule behavior; this refactor does not approve new rounding or budget semantics. Raw transaction identities remain single-chain until the M1 schema work explicitly changes them. Historical specifications are design inputs only; conflicts route through [authority resolution](ENGINEERING.md).
