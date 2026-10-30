# Project map

[Root instructions](../AGENTS.md) · [README](../README.md) · [Machine ownership registry](components.json)

Start here; read one component rather than the whole project. Paths and ownership live in the registry; this page provides human navigation. Each component document points back here and to its implementation/tests.

| Need | Maintained document |
| --- | --- |
| RPC, ingestion, checkpoint, replay | [Indexer](../app/indexer/README.md) |
| API, authorization, wallet endpoints | [API](../app/api/README.md) |
| Schema, transactions, migrations | [Database](../app/db/README.md) |
| Budget/anomaly/recipient rules | [Rules](../app/rules/README.md) |
| Templates | [Dashboard](../app/dashboard/README.md) |
| Settings, packaging, containers | [Runtime](components/RUNTIME.md) |
| Fixtures, acceptance testing | [Testing](TESTING.md) |
| Agents, CI/CD, documentation checks | [Workflow and rationale](WORKFLOW.md) |

- [Architecture and current limitations](ARCHITECTURE.md)
- [Requirement status and evidence trace](REQUIREMENTS.md)
- [API, storage and configuration contract authority](CONTRACTS.md)
- [Launch roadmap / future requirements](ROADMAP.md)
- [Operating and recovery runbook](RUNBOOK.md)
- [Dated verification evidence](VERIFICATION.md)
- [Engineering philosophy and decision policy](ENGINEERING.md)
- [Persistent reviewer role](../.codex/agents/arc-reviewer.toml)
- [Research and sources](REFERENCES.md)
- [Review skill](../.agents/skills/arc-review/SKILL.md)
- [Development skill](../.agents/skills/arc-change/SKILL.md)

Historical `SPEC-v1.0-2026-09-16.md` and `EXECUTION-PROMPT.md` are archived design inputs. They are not maintained runtime contracts; resolve conflicts using current code/tests, component docs and the roadmap.

For conflicting sources, use the [authority and update map](ENGINEERING.md). Component ownership is defined once in the registry; do not create a second ownership list in task instructions.
