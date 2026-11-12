# Api component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

Wallet registration, ledger reads and API-key dependencies. Reads currently expose labels/activity. Test write authorization, validation and pagination; do not claim tenant isolation.

## Entry points

- [app/api](../../app/api)

## Verification

- [tests/integration/test_api.py](../../tests/integration/test_api.py)

## Flow

`HTTP request → validation/auth dependency → route → database query/write → response`. Dashboard routes render the stored monitoring view. Inspect [agent routes](routes_agents.py) and [dependencies](deps.py) before changing a boundary.

## Constraints and decisions

FastAPI/Pydantic provide request validation; SQLAlchemy shares the service database. Write routes require an API key; database-backed keys also support salt-based verification. Existing reads are not tenant-isolated. Extending authorization requires an explicit product decision and positive/negative route tests, rather than assuming a wallet address proves ownership. Original selection dates are unknown.

## Pitfalls

A valid key does not establish per-tenant row ownership. Health/lag output is an operational signal, not proof that RPC data is canonical or the system meets an uptime target. Never expose configured secrets in responses or logging.

`GET /api/v1/health` is defined in [the application entrypoint](../main.py). Its existing HTTP 200/body-status contract now includes `indexer.recovery_required`, `recovery_reason`, `common_ancestor_block` and `recovery_detected_at` (UTC timestamp or null). A persisted recovery reason always makes body status `degraded`, independent of lag and RPC availability. Null recovery fields mean no recorded latch, not proof that a stopped worker is healthy. The API is read-only for this state; it provides no reset endpoint. Existing ledger endpoints still expose stored rows and do not filter or repair orphaned history.

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): define read access/tenant isolation and pagination limits before public launch; acceptance includes rejected cross-tenant reads and bounded queries. These controls are future work.

## Acceptance scenarios

- Given a write request without a valid key, when sent to a protected route, then it is rejected. Evidence: `test_write_route_requires_api_key` and `test_db_backed_api_key_auth` in the suite below.
- Given an existing registered wallet, when registered again, then the conflict is explicit. Evidence: `test_duplicate_wallet_conflict`.
- Given stored monitoring data, when requesting summary/transactions, then the response reflects stored rows. Evidence: `test_agent_summary_and_transactions`; this is an API integration scenario, not a browser/live-chain E2E run.
- Given a persisted fork state, then health reports the reason, ancestor and detection time and stays degraded at zero lag or when RPC is unavailable. Given no latch and zero lag, the existing healthy response remains. Evidence: `test_health_exposes_durable_recovery_even_at_zero_lag`.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
