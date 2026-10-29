# Dashboard component

[Project map](../../docs/INDEX.md) · [Ownership registry](../../docs/components.json)

Jinja dashboard templates. Keep presentation consistent with API behavior and escape untrusted labels. Check rendered HTML and relevant routes when changing presentation.

## Entry points

- [app/dashboard](../../app/dashboard)

## Verification

- [tests/integration/test_api.py](../../tests/integration/test_api.py)

## Flow

`dashboard route → stored summary/query result → Jinja template → HTML response`. Follow API ownership for query/auth changes; this component owns presentation templates.

## Constraints and decisions

Server-rendered Jinja avoids adding a separate frontend build stack for the current monitoring view. Preserve escaping for labels and distinguish missing data from zero values. This is the current design, not a measured comparison against a SPA; older decision history is unknown.

## Pitfalls

Rendered HTML can disagree with API semantics after a field change. A successful page render does not establish browser interactions, accessibility or tenant privacy. Never mark untrusted labels safe to bypass escaping.

## Backlog

Define browser-level acceptance/accessibility checks when interactive controls are added. Public-launch access boundaries follow the [roadmap](../../docs/ROADMAP.md); do not present private information as harmless just because it is read-only.

## Acceptance scenarios

- Given stored monitoring data, when GET /api/v1/dashboard renders, then the response succeeds and includes the monitoring view. Evidence: `test_dashboard_renders` in the route suite below.
- Browser/device/accessibility E2E and dedicated malicious-label rendering coverage are still missing; add evidence with the corresponding behavior change.

## Maintenance contract

Any change to owned non-document files must update this document in the same PR. Explain changed behavior, invariants, evidence or why the external contract stays the same; a timestamp-only edit is insufficient. See the project workflow for review and launch gates.
