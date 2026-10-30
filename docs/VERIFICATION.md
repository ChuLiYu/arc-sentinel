# Verification record

[Project map](INDEX.md)

## Flywheel refactor and M1 foundation — 2026-10-30

Local candidate on `refactor/flywheel-m1-foundation`, based on `6751283cd95f55567b35533168adeb9539450567`. The pre-change full baseline passed 204 tests. This change retains the stack/router/component ownership, adds authority/requirement tracing and strengthens maintained-document validation, then implements the first M1 lineage slice.

| Check | Result | Boundary |
| --- | --- | --- |
| `./scripts/verify.sh` | Mapping/links and Ruff passed; **231 tests passed**, no skips | Real disposable PostgreSQL + mocked RPC; not live-chain verification |
| `python3 scripts/project_check.py --base origin/main` | Passed for complete candidate diff | File ownership/co-change and local file links, not prose truth |
| Knowledge gate regression | **22 tooling tests**, included above | New broken-link and archive-policy cases failed before repair; no semantic business approval inferred |
| M1 failure and migration tests | Included above | Parent/member/range/boundary, caught-up fork, legacy checkpoint, provider switch/retry, empty-wallet continuity and populated `0001` upgrade |
| Local image `arc-sentinel:flywheel-m1` | Build and offline smoke passed | UID 10001, app/journal model import and `0002` migration present; no registry publication |
| Scoped independent read-only review | Sole malformed-block-number exception finding fixed and confirmed; focused worker/RPC suite **78 passed** | Reviewer ran static/unit/tooling checks; implementer ran full PostgreSQL suite and build |

Regression demonstration before the M1 fix: five new cases failed on the old worker (parent discontinuity, wrong receipt block, out-of-range log, changed batch boundary and caught-up checkpoint fork). The new cases now refuse progress and preserve rows/journal/checkpoint. Malformed header quantities/hashes/timestamps and mixed-case transaction hashes also have unit coverage. Populated baseline upgrade preserves existing rows and leaves the journal empty rather than inventing historical evidence.

The [worked change path](ENGINEERING.md) was executed for the journal slice. The coverage inventory there retains every runtime component and identifies contract, knowledge and historical-input authority. Historical prototype rule approval is still unknown, and the [requirement trace](REQUIREMENTS.md) preserves that distinction.

At this initial local verification, no live RPC, signing, cloud resources, production migration, remote push or deployment was used; remote CI had not run for the candidate. Later delivery evidence is recorded separately below. Automatic fork rollback, raw chain-scoped identities, wallet ledger and persisted recovery readiness remain M1 work. Schema upgrade alone cannot resume a legacy height-only checkpoint; see [runbook](RUNBOOK.md).

## Delivery resumption after directory migration — 2026-10-30

The repository is now at `/Users/liyu/Personal/repos/arc-sentinel`. Resumed the already-reviewed local foundation at `3c5dcd3`; this delivery step changes operational documentation, not indexer behavior.

- First canonical verification stopped at the Docker readiness check because the daemon socket was unavailable. Unit/tooling checks independently passed **194 tests**. After ensuring the existing OrbStack engine was ready, `./scripts/verify.sh` passed mapping/links, Ruff and **231 tests with no skips** (35.45 seconds), including disposable PostgreSQL suites.
- `python3 scripts/project_check.py --base origin/main` passed for the complete candidate diff. These checks work from the new physical directory; no tracked runtime path change was needed.
- GitHub confirmed repository-scoped protection on `main`: required GitHub Actions checks `test`/`docker`, strict up-to-date checking, administrator enforcement, force pushes disabled and branch deletion disabled. Required human/Code Owner approval remains off for the single-maintainer workflow. The [workflow](WORKFLOW.md) owns the current dated configuration and limitations.
- The remote repository was confirmed private. Existing [PR #1](https://github.com/ChuLiYu/arc-sentinel/pull/1) is the delivery/remote-CI evidence location; check its head SHA and associated run rather than reusing an older green result. Local test evidence above does not assert a future GitHub Actions result.

No live-chain request, signing, cloud deployment or production data migration was performed. M1 remains incomplete as listed in the [requirement trace](REQUIREMENTS.md); this delivery does not imply automatic fork repair or wallet-ledger support.

## Previous baseline — 2026-10-29

Local verification on 2026-10-29, branch `feat/launch-readiness`:

| Check | Result | Boundary |
| --- | --- | --- |
| `./scripts/verify.sh` | Ruff passed; **204 tests passed** | Disposable PostgreSQL + mocked RPC; not a live-chain test |
| Governance/tooling suite | **20 tests passed**, included in full run | Temporary Git histories + selected CI expressions; not a GitHub event simulator |
| Recovery suite | Included in full run | Missing block/receipt/log, rollback after inserts, competing writers, wrong chain, inclusive start |
| Container build | Passed, uv 0.12.10 / frozen lockfile | Local Docker architecture; base images not digest-pinned |
| Container smoke | UID 10001, application import and migrations directory present | Does not prove external RPC connectivity or live availability |

No real RPC credentials, funded wallet or cloud resources were used for these checks. CI results on the pull request are the independent Linux verification; this document records the local run rather than inventing future CI results.

## Governance evidence added 2026-10-29

- Component mapping/co-change/local file links and five knowledge sections pass against `origin/main`; both local skill definitions pass skill-creator validation.
- Policy fixtures cover omissions, Markdown policy updates, registry changes, ambiguous/unmapped files, deleted/renamed paths, broken/backward links, invalid base and an independently advanced base.
- Regression demonstration: in a disposable copy, restoring the old Markdown-policy exemption caused both agent-policy cases to fail as expected (2 failures); the final implementation passes.
- CI configuration contract tests require both jobs to use the same head and preserve PR-base/push-before wiring. YAML structure and verification-script Bash syntax also validated locally.
- Independent read-only review identified policy-Markdown exemption and mismatched CI checkouts; corrected both and added coverage. Follow-up independent review reported no material actionable findings; it reran the doc gate, Ruff and 20 tooling tests, while the implementer ran all 204 tests. CODEOWNERS and GitHub enforcement limitations are recorded in the workflow. No GitHub protection setting was enabled.
- The persistent reviewer TOML parses; this existing session used the available reviewer agent rather than asserting hot-reload/discovery of the new role. Model-based CI, scheduled scans and live deployment were not added.

## Reproduce

```sh
uv sync --frozen
./scripts/verify.sh
docker build -t arc-sentinel:check .
docker run --rm arc-sentinel:check python -c \
  'import os; from app.main import app; assert os.getuid() == 10001; print("non-root app import OK")'
```

The local Docker credential helper could not access public registry credentials during the first build. Building against the same daemon with an empty temporary Docker configuration succeeded without registry credentials. This was an environment issue, not a change to stored credentials.

## Evidence still required

- Canonical-chain rollback/reorg and derived-data reconciliation tests.
- Multi-wallet transfer ownership and chain-scoped identities.
- Restore drill with measured recovery time and data-loss window.
- Actual testnet RPC comparison, provider failure/recovery and multi-day operation.
- Reproducible load dataset and measured throughput/latency/resource usage.
- TypeScript cross-chain lifecycle workflow, duplicate/reorder/crash tests.

Do not replace these missing measurements with aspirational throughput or availability numbers. Each launch milestone in ROADMAP.md defines its own acceptance evidence.
