---
name: arc-review
description: Review Arc Sentinel diffs for correctness, regression risk, test gaps and documentation drift. Use for code review, not implementation.
---

Read [AGENTS.md](../../../AGENTS.md) and the affected entries from [the map](../../../docs/INDEX.md). Remain read-only. Establish the base/head and changed paths; run `python3 scripts/project_check.py --base <base>` first. Read the diff, owning component docs, changed tests, and direct callers as needed. Do not load the entire repository or historical SPEC by default.

Prioritize concrete failure paths: incomplete RPC data, duplicate/reordered delivery, rows/checkpoint atomicity, competing writers, chain/reorg assumptions, migration compatibility, auth boundaries, secret exposure and deploy rollback. Check that tests would fail on the old faulty behavior and assert observable invariants. Verify component docs describe the final behavior, not just that a file changed.

Return findings ordered by severity: P0 immediate critical incident, P1 likely data loss/security/outage, P2 material defect, P3 minor actionable defect. Each needs file/line, trigger, consequence and suggested validation. Omit speculative/style-only findings covered by tools. If none, say no actionable findings and list verification limits. Do not assert unexecuted checks passed. Do not fix code or deploy during review. When unavailable as a separate agent, disclose self-review instead of claiming independence.
