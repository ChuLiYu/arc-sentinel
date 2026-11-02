# Arc Sentinel

A read-only EVM wallet activity indexer and monitoring API, built with Python 3.12, FastAPI, PostgreSQL, SQLAlchemy, and HTTP JSON-RPC. This repository is a backend portfolio prototype; the source alone does not establish production usage, throughput, or availability.

## Implemented behavior

- Poll blocks and USDC Transfer logs for registered wallets, with a configurable confirmation buffer and bounded catch-up batches.
- Insert transactions and transfers with PostgreSQL conflict handling; commit the indexing checkpoint with the indexed rows.
- Retry RPC requests with backoff, treat throttling separately, and support a fallback provider.
- Evaluate budget, recipient, and anomaly rules and expose agent, transaction, alert, and statistics endpoints.
- Authenticate write endpoints using hashed API keys. Provide a dashboard, migrations, backup scripts, and container deployment definitions.

```text
EVM JSON-RPC -> indexer -> PostgreSQL -> FastAPI -> dashboard/API clients
                              ^           |
                              +-- rules --+
```

## Local development

Prerequisites: Python 3.12, uv, and a running Docker daemon for PostgreSQL integration tests.

```sh
uv sync --frozen
uv run ruff check app tests
uv run python -m pytest tests/unit -q
# Starts a disposable PostgreSQL container on port 15432:
./scripts/verify.sh
```

For an interactive API, create a local PostgreSQL database and configure `DATABASE_URL` in an untracked `.env`, then:

```sh
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# In another terminal, after configuring a matching RPC URL and chain:
uv run python -m app.indexer.worker
```

API documentation: `http://127.0.0.1:8000/docs`. Default code settings target a test network. Historical network and provider claims in `.env.example` and the specification require independent verification before use; configure the RPC URL, chain ID, and token address for the same network. Do not copy real credentials into tracked files.

## Guarantees and limitations

- Database uniqueness prevents duplicate stored rows; this is not a claim of end-to-end exactly-once processing.
- A confirmation buffer is implemented, but canonical block-hash tracking and chain reorganization rollback are not implemented.
- A new indexer starts at the current safe head; automatic full-history backfill is not provided.
- Checkpoint coordination across multiple concurrent indexer instances needs additional locking or leasing.
- The rules loop runs in the API process; it is not a durable workflow engine.
- OCI-oriented deployment files are included. AWS deployment and cross-chain message processing are not implemented.
- CI tests and builds pull requests; image publication is restricted to `main`. The historical deployment workflow is disabled pending a separate deployment review.
- Read endpoints are public; tenant isolation, rate limiting, and bounded cache eviction are not implemented. Review [the security scope and operational limits](SECURITY.md) before running the prototype.

## Verification recorded on 2026-11-01

`./scripts/verify.sh`: Ruff passed; **154 tests passed**, including integration tests against disposable PostgreSQL. A `pip-audit` scan of all registry packages in `uv.lock` reported no known vulnerabilities. Git-history credential scanning found only a synthetic example salt; no real committed credential was identified. These checks have limited scope; see [SECURITY.md](SECURITY.md). No live-chain or load benchmark was performed.

## Next engineering work

1. Persist block hashes and parent hashes; test shallow/deep reorg recovery, replay, and interrupted commits.
2. Add a single-writer lease and tests for competing workers.
3. Separate rule execution into a recoverable worker; add retry budgets, deduplication, and operational metrics.
4. Add a TypeScript cross-chain message observer with durable workflow execution, mocked failure scenarios, and a testnet demonstration.
5. Add an AWS deployment with measured cost, lag, recovery time, and reproducible benchmark instructions.

See [the existing specification](docs/SPEC-v1.0-2026-09-16.md) for design context. Planned features and historical notes there are not evidence of deployed behavior.
