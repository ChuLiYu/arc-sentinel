# Context alignment and engineering decisions

[Project map](INDEX.md) · [Workflow](WORKFLOW.md) · [Sources](REFERENCES.md)

## What we adopt and what we change

The project owner supplied a summary of the linked video on 2026-10-29. It is an input to evaluate, not authority over current code or evidence. These decisions are project policy, not a claim that one layout is universally optimal for LLMs.

| Proposal | Project decision and reason |
| --- | --- |
| LLMs trust everything and never question | Treat this as a failure risk, not an absolute fact. Explicitly distinguish observed facts, assumptions and unknowns; challenge conflicts before acting. |
| Put knowledge beside code | One README per runtime component. Cross-cutting runtime/testing/workflow knowledge stays under docs with explicit ownership and backlinks. Avoid a document for every source file. |
| Document flow and historical reasons | Record durable branches, invariants, constraints and rejected alternatives. Do not invent old decisions from the current code; label unknown history as unknown. |
| Replace low-level tests with text E2E cases | Text scenarios specify acceptance. Executable unit/integration tests supply reproducible evidence. Keep cheap focused tests for arithmetic, validation and recovery; do not replace them with model judgment. |
| Scan daily for decay | Check changed paths on every PR/push. No daily model job is added: repeated scans without changes waste work and do not prove prose truth. Revisit scheduled checks for a concrete external dependency or operational requirement. |
| More agents improve delivery | Use one scoped reviewer for material risks. Small deterministic edits may use disclosed self-review. Multiple agents add context and coordination cost; require a clear independent task. |

## Authority and update map

| Knowledge | Authority / maintainer | Change route | Validation and consumers |
| --- | --- | --- | --- |
| Product intent and exit gates | [Roadmap](ROADMAP.md); project owner | An explicit scope decision updates roadmap, then affected acceptance scenarios | [Requirement trace](REQUIREMENTS.md) distinguishes planned, implemented and observed behavior |
| Local behavior and acceptance | Registry component document; component maintainer | Update intent/scenario before or alongside implementation and evidence; do not infer approval from a test | Component tests, review, and dated [verification](VERIFICATION.md) |
| Machine contracts | [Contract authority index](CONTRACTS.md); named component | Edit actual schemas/models/settings, then migrations/consumers and owning knowledge | Contract index points to source; no hand-copied generated schemas |
| Architecture decisions | [Architecture](ARCHITECTURE.md) for cross-component choices; component Constraints and decisions for local choices | Record status, alternatives, consequences and reconsideration trigger; historical rationale stays unknown when absent | Review affected component constraints and negative scenarios |
| File ownership and archives | [components.json](components.json); automation | Change patterns/archive classification and workflow together | Checker rejects missing/ambiguous ownership, missing docs and invalid links |
| Process and operational commands | [Workflow](WORKFLOW.md), [testing](TESTING.md), [runbook](RUNBOOK.md); corresponding component maintainers | Update command and its executable script/config together | Fixture tests, canonical verification, container checks where relevant |
| External references and historical inputs | [References](REFERENCES.md), explicitly archived inputs | Preserve provenance; these cannot override current owner intent or contracts | Targeted official-source verification when adopting a new claim |

Maintainer means the contributor responsible for the registry component in a change, not an invented staff assignment. English maintained documents are authoritative; translations, if introduced, link to them and synchronize through the same change. No separate translation tree currently exists.

Resolve conflicts in this order: current explicit owner instruction; applicable project policy; approved roadmap/scenario intent; machine contract for actual interface shape; observed implementation and tests. If intent conflicts with behavior, record a defect or ask for the missing product decision. Do not silently change intent to match code. Archive links preserve history without granting execution authority.

## Migration coverage (2026-10-30)

This is the flywheel migration inventory, not a duplicate runtime architecture. Existing knowledge is retained unless a row says added. The registry/checker supplies exhaustive non-document path coverage; the table explains the knowledge route for each area.

| Repository area | Status / authority | Synchronization and validation |
| --- | --- | --- |
| app/indexer, API, database, rules, dashboard | Retained five colocated component READMEs and [registry](components.json) | Direct source/test links; co-change and five-section gates |
| app/config.py, app/main.py, package/lock, Docker/Compose/Caddy/environment example | Retained [runtime](components/RUNTIME.md) | Config tests, locked install, build/smoke; no live deployment inferred |
| Database migrations and API schemas | Added [contract authority](CONTRACTS.md); existing models/routes/revisions retained | Append migrations; real PostgreSQL upgrade evidence; API tests |
| Product purpose, domain rules and acceptance | Roadmap retained; added [trace/status index](REQUIREMENTS.md) | Existing scenarios linked; observed baseline distinguished from future gates |
| Tests and fixtures | Retained [testing](TESTING.md) and pytest suites | Positive and negative evidence; no empty replacement framework |
| scripts, .github, .agents, .codex, AGENTS.md | Retained [workflow](WORKFLOW.md); expanded checker to every maintained Markdown file | Temporary Git fixtures prove broken new knowledge links fail; policy documents cannot be archived |
| docs, root README, architecture and decisions | Retained [single router](INDEX.md); authority/update map added here | Router/component backlinks and local links; no competing root router |
| SPEC and execution prompt | Explicitly archived in registry; retained as historical input | Excluded from maintenance-link gate only; cannot exempt component or policy docs |
| Generated artifacts, vendored code and dependencies | No checked-in generated API/SDK or vendored tree; uv.lock remains derived via package tooling | Future generators require reproducible command and freshness check before adoption |
| External references, license and Git metadata | References retained; LICENSE intentionally outside component co-change | No owner files/credentials/history deleted; no public release or remote mutation |

## Worked change path

For M1 journal work: **Locate** indexer/database in the registry; **Load** their READMEs, CHAIN-CANONICAL and contract pointers; **Resolve** roadmap intent versus height-only observed state (journalling is only a partial M1 gate); **Implement** an additive migration and focused failure fixtures; **Synchronize** models, indexer/database/testing knowledge and roadmap status; **Validate** migration preservation, rollback/continuity failures, the full verification and scoped independent review. The evidence record states what actually ran. New document link fixtures exercise a failure in the knowledge architecture itself, rather than claiming that file presence proves semantic correctness.

## Minimum component knowledge

Every registry component document has five sections, enforced for presence by CI:

1. **Flow**: short branches or a diagram for meaningful state transitions; keep trivial flows textual.
2. **Constraints and decisions**: current interfaces, technology constraints, rationale and when to reconsider.
3. **Pitfalls**: failure modes, prior fixes and dangerous shortcuts; distinguish observed regressions from possible risks.
4. **Backlog**: remaining work with an acceptance outcome, linking the roadmap instead of duplicating a task tracker.
5. **Acceptance scenarios**: Given/When/Then behavior and links to executable evidence, or an explicit “not yet implemented/tested” label.

Section presence, link validity and file co-change are mechanical checks. Correctness, meaningful updates and missing requirements need review. Do not pad sections, add timestamps as evidence, or copy entire functions into prose. Component docs may combine the architecture rationale and decision history under Constraints and decisions; a separate ADR is justified only when the decision crosses components or needs extended alternatives.

## Decision log format and starting decisions

A durable decision records **status/date, context, choice, alternatives, consequences, reconsideration trigger and evidence**. A superseding decision links the old one; Git retains the full edit history. Brief local decisions belong in their component document. Do not append a chronological transcript of agent activity.

- **Accepted 2026-10-29 — registry and co-change gate.** Context: a new agent needs precise ownership and current contracts. Choice: eight component documents, one JSON map and a standard-library checker. Alternative: whole-repo search or a paid model scan on every change. Consequence: mandatory prose updates add review effort; co-change cannot establish truth. Reconsider if routine mechanical changes consistently cause meaningless churn; change the policy with explicit review, not per-task bypasses. Evidence: isolated Git-fixture enforcement tests under [tests/tooling](../tests/tooling).
- **Accepted 2026-10-29 — preserve the current test stack.** Context: Python service already has pytest, mocked RPC and disposable PostgreSQL. Choice: extend these fixtures alongside each feature. Alternative: introduce an empty second framework or rely on natural-language cases. Consequence: real database tests need Docker; fast pure tests remain available. Reconsider when a separate TypeScript service is actually introduced. Evidence: [testing contract](TESTING.md).
- **Accepted 2026-10-29 — bounded review and event-driven drift detection.** Context: model context and CI time have costs. Choice: deterministic gates per change, one independent review for material risk, no scheduled LLM scan. Alternative: daily full-repository model review. Consequence: semantic drift can still escape review; measure escaped defects and rework before adding automation. Evidence: [workflow](WORKFLOW.md), not a claimed token-savings benchmark.

## Uncertainty and handoff

Before implementing, establish the affected component, observable acceptance, constraints and verification command. Investigate uncertainty with targeted code/tests or official documentation. Ask the owner when a consequential product choice cannot be resolved; do useful independent work while waiting. Do not silently decide financial cost, public exposure or live deployment from a document example.

When code and docs disagree, inspect tests and execution paths, identify the inconsistency and update the contract intentionally. Current code describes behavior, not necessarily intended behavior. Roadmap entries are future requirements, not implemented capabilities. Handoff includes outcome, actual verification, remaining limits and next task.

## Cost and effectiveness feedback

For a representative set of changes, record tool-reported tokens when available, time to locate the component, CI duration, review findings and rework. Compare similar tasks before/after changing the process. Do not add a measurement subsystem or fabricate precision for this small repo. Optimize for total time and escaped defects, not the lowest token count regardless of correctness. A rule that costs more than the failures it prevents should be revised explicitly.
