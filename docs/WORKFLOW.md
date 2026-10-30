# Development, review and automated gates

[Project map](INDEX.md) · [Root rules](../AGENTS.md) · [Registry](components.json) · [Checker](../scripts/project_check.py) · [CI](../.github/workflows/ci.yml)

## Small change loop

Acceptance criteria → focused failing test → implementation → component docs → focused test → full gate → read-only review → PR. Use [arc-change](../.agents/skills/arc-change/SKILL.md) for execution and [arc-review](../.agents/skills/arc-review/SKILL.md) for review. These are repo-local Codex skills, not a running service or a new model. The persistent [arc-reviewer](../.codex/agents/arc-reviewer.toml) role delegates to the review skill; ask Codex to use that agent in a supported client. It inherits the parent model/settings; discovery in a future session depends on client support and has not been exercised by this session. A role prompt does not enforce filesystem permissions; use a read-only reviewer environment when available.

Request example: “Use arc-review to inspect base...HEAD. Read only the changed components. Report actionable findings; make no edits.” For independent review give the reviewer acceptance criteria, diff and affected docs, without the implementer's preferred verdict. Reviewer output is evidence to assess, not permission to deploy.

## Documentation contract

`components.json` maps non-Markdown files and policy Markdown (AGENTS.md and .agents/**) to one component and its maintained document. Changes to either require that document in the same PR diff. All tracked non-Markdown files except LICENSE, plus these policy Markdown files, must be mapped; newly added or deleted paths and renames are checked too. A deleted document fails. Ordinary documentation-only edits do not recursively require more docs. Policy Markdown remains subject to co-change. The five knowledge sections are required structurally; see [engineering decisions](ENGINEERING.md). New component docs must link the root map, which must link them back; source/test links must resolve.

Update behavior, invariants, interfaces, acceptance cases or operational consequences. When behavior stays the same, explain why and identify the validation. Timestamp-only edits do not meet the review contract. CI detects file-level co-change and broken local links, not prose truth or anchor validity. A reviewer must still compare code/tests/docs. Every tracked or untracked maintained Markdown file is checked, including newly added knowledge outside the original document list. Historical inputs are explicitly listed in the registry; required root/component documents and agent policy cannot be archived. Archive classification itself changes automation policy and requires this document. The registry now links the database component to its populated-baseline migration test; fresh schema creation alone is insufficient upgrade evidence. See the [authority map](ENGINEERING.md) and [contract sources](CONTRACTS.md).

## CI/CD and cost

[CI](../.github/workflows/ci.yml) fetches history, cancels superseded runs on a branch/PR, validates component mapping, local Markdown file links and changed-doc presence, then runs lint and tests. It builds an image after those pass. Test reports are uploaded only on failure. No LLM API call, API key or per-review model charge is added to CI. GitHub runner/storage costs can still apply.

[verify.sh](../scripts/verify.sh) is the same local entrypoint. Pass `--base <ref>` to project_check for the full PR; default HEAD inspects current working/staged/untracked changes. CI uses the PR base or push-before SHA to find the common ancestor with HEAD; unrelated changes added to main after divergence are excluded. Both test and Docker jobs validate the same exact PR head. This does not test a synthetic merge result; keeping the branch updated or using a merge queue remains a separate integration decision. Missing/invalid base fails visibly. This bootstrap PR adds the initial component docs, so historical source changes in the same PR are covered.

CD stays manually enabled with a tested revision, pinned SSH identity and pre-provisioned configuration; see [runbook](RUNBOOK.md). No automatic production rollout is added. Green CI is a prerequisite; protected-branch required checks are an administrator setting, not enforced merely by this YAML. Configure required checks `test` and `docker` on main before relying on merge prevention. [CODEOWNERS](../.github/CODEOWNERS) routes governance changes to @ChuLiYu. Add another eligible maintainer before enabling required Code Owner review: PR authors cannot approve themselves. Then require that review for rules, skills, registry, checker and workflows. Neither required checks nor required reviews are enabled by this patch. A contributor can weaken the gate in the same PR; repo-local rules are not a security boundary against authorized malicious changes. Review of the governing files and GitHub protection are separate controls; no account-wide setting is changed here.

## Token discipline

Read root rules → map → affected doc → diff → direct callers. Expand only to resolve a specific uncertainty. Keep logs concise; use failing test IDs and assertions instead of dumping successful logs. Deterministic checks find mapping/style/test failures; model reasoning handles semantics. Use one targeted independent review for material risk rather than repeatedly launching full-context agents. No token-savings percentage is claimed until measured.

Handoff: behavior changed; tests/commands and actual results; unresolved limits; next task. Git history and PRs hold progress; do not append endless session transcripts to AGENTS.md.

## Flow

`acceptance + affected map → focused test/change → component docs → deterministic CI → scoped review → explicit merge/deploy decision`. Concurrent writers must own distinct files; the integrator resolves conflicts, reruns affected checks and verifies all component docs after integration.

## Constraints and decisions

The checker uses only Python's standard library, so missing optional dependencies cannot disable the first gate. Git base validation fails closed; comparison starts at the common ancestor to avoid attributing unrelated base-branch work to a PR. Both CI jobs check out the same head revision; the complete history supplies PR-base/push-before comparisons. A gate protects honest mistakes, while protected review is needed to protect the gate itself.

## Pitfalls

Review on 2026-10-29 found that generic Markdown exemption also exempted agent policy, and Docker checked a different revision than tests. Both were corrected with regression coverage. Updating a file or adding a required heading does not prove its contents accurate. A read-only instruction does not restrict shell writes mechanically.

## Backlog

Enable appropriate GitHub protection when maintainer availability permits; evaluate token/time/rework measurements from comparable tasks before introducing more agents. No daily LLM scan or automatic deploy is configured. Add a merge-queue test only when that integration workflow is adopted.

## Acceptance scenarios

- Given a code or agent-policy change without its component doc, when checked against the PR base, then the gate fails. After meaningful docs are included, it passes.
- Given a new unmapped file, deleted/renamed path, missing knowledge section or broken backlink, then CI rejects the drift. Evidence: [Git-fixture tests](../tests/tooling/test_project_check.py).
- Given PR or push events, test and Docker use the same head expression and the doc gate uses base/before SHA. Evidence: [configuration contract tests](../tests/tooling/test_workflow_contract.py). These assert config wiring, not a full GitHub event simulator.
