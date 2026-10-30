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

## Backlog

[Launch roadmap](../../docs/ROADMAP.md): define read access/tenant isolation and pagination limits before public launch; acceptance includes rejected cross-tenant reads and bounded queries. These controls are future work.

## Acceptance scenarios

- Given a write request without a valid key, when sent to a protected route, then it is rejected. Evidence: `test_write_route_requires_api_key` and `test_db_backed_api_key_auth` in the suite below.
- Given an existing registered wallet, when registered again, then the conflict is explicit. Evidence: `test_duplicate_wallet_conflict`.
- Given stored monitoring data, when requesting summary/transactions, then the response reflects stored rows. Evidence: `test_agent_summary_and_transactions`; this is an API integration scenario, not a browser/live-chain E2E run.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
