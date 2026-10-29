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
