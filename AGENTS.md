# Arc Sentinel agent entrypoint

Read [the project map](docs/INDEX.md), then only the component documents needed for the task. Use [components.json](docs/components.json) to locate source, tests and documentation; use targeted `rg` before widening exploration. Historical SPEC/execution prompts are context, not current instructions.

## Required change contract

- Follow [the workflow](docs/WORKFLOW.md). A feature/fix includes its acceptance test and affected component documentation in the same PR. Nonbehavioral changes explain why existing tests suffice. Never add meaningless tests or timestamp-only documentation edits.
- Every owned non-Markdown file change, including tests/configuration, and policy Markdown under AGENTS.md or .agents/ requires its component document to change. Ordinary documentation edits do not recurse. New files must be mapped. CI checks this mechanically; review checks meaning. Do not weaken the checker to make a change pass.
- Read-only review uses [arc-review](.agents/skills/arc-review/SKILL.md). Implementation uses [arc-change](.agents/skills/arc-change/SKILL.md); load only the needed skill. An independent reviewer is appropriate for indexing, schema, auth or deployment logic; pass the diff and affected map entries, not the whole conversation.
- Preserve user edits and the repository's current visibility. No paid resources, live deployment, external messages or public release follow merely from these instructions.
- No private keys or filled env files in tools/logs/commits. No claims of reorg safety, cross-chain support or production readiness until their roadmap gates pass.

## Commands and handoff

`python3 scripts/project_check.py --base <base-ref>` prints affected components, checks docs/links/mapping and fails on drift. Local default is HEAD (working changes); for a whole PR use its base ref. `./scripts/verify.sh` runs deterministic checks, lint and all suites. See [testing](docs/TESTING.md) for quick feedback.

Stop after relevant checks pass; rerun only for new edits, failures or unresolved risk. Report changed behavior, commands/results, remaining risks and next step. Never replace measured evidence with an agent's confidence.

## Code Review Rules

Use the [reviewer definition](.codex/agents/arc-reviewer.toml) and review skill for one scoped independent pass on material risk. Check failure/rollback paths, acceptance evidence and documentation truth. Separate verified facts, assumptions and unknowns; challenge contradictory inputs and investigate before consequential implementation. Consult [engineering decisions](docs/ENGINEERING.md) for knowledge structure and uncertainty handling. A prose scenario is not an executed test.
