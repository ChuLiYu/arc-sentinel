---
name: arc-change
description: Implement or fix Arc Sentinel behavior with acceptance tests and synchronized component docs. Use for repository development, not standalone reviews.
---

Read [AGENTS.md](../../../AGENTS.md), [the workflow](../../../docs/WORKFLOW.md) and only affected component entries from [the map](../../../docs/INDEX.md).

1. Define observable acceptance criteria and failure cases before implementation. Find the existing test fixture; extend it instead of introducing another framework. For a defect, demonstrate a failing regression test first when practical.
2. Implement one coherent slice. Indexer/schema changes need real PostgreSQL recovery tests; pure decoding/rules use unit tests; API changes need route/auth assertions. Add migrations with schema edits.
3. Update every affected component document with behavior/invariant/evidence changes, or explain unchanged contracts for mechanical edits. Add mappings for new components and link back to the root map. Preserve the five knowledge sections: flow, constraints/decisions, pitfalls, backlog and acceptance scenarios. Record rationale and unknowns without inventing history. Link text scenarios to executed tests or label missing evidence. Do not duplicate whole source files in docs.
4. Run focused tests while iterating. Run the canonical verification once for the final patch and project_check against the PR base. Investigate failures; do not skip tests or reduce requirements to get green.
5. For risky behavior, request an independent read-only pass using arc-review and only the diff + relevant docs. Address findings, retest changed behavior, then hand off with evidence. Deployment and merge follow existing user authorization, not this skill.
