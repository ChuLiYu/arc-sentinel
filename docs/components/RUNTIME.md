# Runtime component

[Project map](../INDEX.md) · [Ownership registry](../../docs/components.json)

Settings, application lifecycle, packaging, containers and network configuration. Arc-native gas assumptions are not portable to ETH gas. Never inspect or commit filled environment files. Validate Compose with dummy settings; deploy remains manual.

## Entry points

- [app/config.py](../../app/config.py)
- [app/main.py](../../app/main.py)
- [app/__init__.py](../../app/__init__.py)
- [Dockerfile](../../Dockerfile)
- [.dockerignore](../../.dockerignore)
- [.env.example](../../.env.example)
- [Caddyfile](../../Caddyfile)
- [pyproject.toml](../../pyproject.toml)
- [uv.lock](../../uv.lock)
- [.gitignore](../../.gitignore)

## Verification

- [tests/unit/test_config.py](../../tests/unit/test_config.py)
- [tests/integration/test_api.py](../../tests/integration/test_api.py)

## Flow

`validated settings → application lifecycle → routes + background runner`; the indexer is a separate process. `frozen dependencies → non-root container → manually enabled deployment`. Configuration errors should stop startup.

## Constraints and decisions

Python 3.12 and the frozen uv lockfile are the reproducible runtime. The container uses UID 10001; pinned uv avoids installer drift, while base image digests remain unpinned. Arc native-gas assumptions are chain-specific. Revisit portability only with chain-specific fixtures and schema decisions, not a renamed environment variable.

`REORG_MAX_DEPTH` defaults to 64 and accepts 1–10000; it bounds diagnostic ancestor reads and does not activate rollback. Apply migration `0003` before running this API/worker revision. Health adds durable recovery reason, ancestor and detection time; any recovery latch forces `degraded` even at zero lag or during an RPC outage. The HTTP status remains 200, so operational consumers must inspect the response body. See the [indexer protocol](../../app/indexer/README.md) and [API](../../app/api/README.md).

## Pitfalls

The local public-registry credential helper failed during verification; an empty temporary Docker config against the same daemon worked without changing stored credentials. Do not bake filled env files into images. Non-root import success is not a live deployment or proof of RPC connectivity.

## Backlog

[Roadmap](../ROADMAP.md): configure real testnet operation, backup/restore and measurable load before launch. Digest-pinning/reproducible platform builds remain work; paid infrastructure and real deployment require existing user authorization.

## Acceptance scenarios

- Given invalid start/confirmation/batch/reorg-search bounds, when settings initialize, then startup rejects them. Evidence: `test_invalid_indexing_bounds_fail_at_startup` below.
- Given a frozen image build, when importing the application as the default user, then import succeeds as UID 10001 with migrations present. Evidence and reproduction: [verification](../VERIFICATION.md).
- Provider failure recovery and multi-day deployed operation remain unverified.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
