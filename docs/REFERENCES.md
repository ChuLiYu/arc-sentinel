# Research and adopted decisions

[Project map](INDEX.md) · [Workflow](WORKFLOW.md)

Reviewed 2026-10-29. These decisions adapt primary sources to this repository; they are not universal requirements or measured token savings.

| Source | Applied here | Deliberate limit |
| --- | --- | --- |
| [OpenAI AGENTS.md guidance](https://developers.openai.com/codex/guides/agents-md) | Root entrypoint and scoped navigation | Instructions guide agents; CI supplies executable enforcement |
| [OpenAI skills](https://developers.openai.com/codex/skills) | Repo-local `.agents/skills` with concise descriptions; load review/development procedure on demand | Defining a skill does not start an autonomous service |
| [OpenAI subagents](https://developers.openai.com/codex/subagents) | Project-scoped TOML reviewer with inherited settings and narrow instructions | More agents consume additional tokens; role instructions do not create read-only filesystem permissions |
| [Anthropic: effective harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Incremental acceptance-driven work, reproducible verification and concise handoffs | No giant transcript or entire backlog loaded into every task |
| [Martin Fowler: Continuous Integration](https://martinfowler.com/articles/continuousIntegration.html) | Automated tests/build, fast feedback, fix failures before promotion | A PR workflow alone is not proof of frequent mainline integration or production readiness |

The [video](https://www.youtube.com/watch?v=R7juGagJWfY) could not be retrieved directly. The owner subsequently supplied a written summary covering co-located knowledge, five artifact dimensions and a synchronization loop. We evaluated that summary in [engineering decisions](ENGINEERING.md); it is user-provided material, not an independently verified transcript.

## Reasoning and tradeoffs

A short map lowers repeated discovery work; component docs hold invariants, not duplicated implementation. A JSON registry supports deterministic ownership checks, including new files. One document per component is maintained rather than one per source file. Each maintained document links back to the project map; CI validates navigation in both directions for component docs.

Document co-change prevents omission but cannot determine whether prose is true. Review verifies semantic consistency. Tests establish behavior but cannot prove all failures absent. AI review complements tests and does not replace them. CI uses no model calls, while local targeted reasoning stays available for difficult correctness questions. Infrastructure still consumes runner time; token savings need measurement before quantification.

A mandatory note for mechanical edits is a deliberate tradeoff requested by the project owner. Avoid meaningless timestamp edits: state the unchanged contract and evidence. Revisit this rule if it produces noisy documents without improving reviews.
