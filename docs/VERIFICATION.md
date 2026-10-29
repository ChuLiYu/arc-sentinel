# Verification record

Local verification on 2026-10-29, branch `feat/launch-readiness`:

| Check | Result | Boundary |
| --- | --- | --- |
| `./scripts/verify.sh` | Ruff passed; **184 tests passed** | Disposable PostgreSQL + mocked RPC; not a live-chain test |
| Recovery suite | Included in full run | Missing block/receipt/log, rollback after inserts, competing writers, wrong chain, inclusive start |
| Container build | Passed, uv 0.12.10 / frozen lockfile | Local Docker architecture; base images not digest-pinned |
| Container smoke | UID 10001, application import and migrations directory present | Does not prove external RPC connectivity or live availability |

No real RPC credentials, funded wallet or cloud resources were used for these checks. CI results on the pull request are the independent Linux verification; this document records the local run rather than inventing future CI results.

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
