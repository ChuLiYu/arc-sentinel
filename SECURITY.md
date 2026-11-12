# Security scope

Arc Sentinel is a backend portfolio prototype. Publishing its source does not
establish production readiness, live-chain correctness, or a security guarantee.

## Public-release checks

The release review covers tracked files and Git history for accidentally committed
credentials, Python dependency advisories, API write authentication, container
packaging, and GitHub Actions publication/deployment boundaries. Local `.env`
files and backups must remain untracked; `.dockerignore` excludes `.env*` from
build contexts. Example salts and hashes in `.env.example` are placeholders and
must be replaced before use.

Dependency fixes include:

- `certifi` 2026.10.23: removes compromised emSign intermediate certificates
  ([GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2)).
- `pytest` 9.2.0: includes the temporary-directory permission fix
  ([GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g)).
  `pytest-asyncio` was updated for compatibility.

The runtime image uses an unprivileged user and a versioned uv build tool.
Pull requests build the image with a read-only token and cannot publish it. A
separate `main`-push-only job holds package write permission and publishes images. The historical deployment workflow is intentionally disabled,
including manual dispatch. Re-enabling it requires a separate review of secret
handling, SSH host verification, configuration, and migration/recovery steps.

## Operational limitations

- Read endpoints are public and expose registered wallet labels and activity.
  Do not store private customer data or expose this API on an untrusted network.
- Write endpoints require an API key but do not provide per-user or per-tenant
  authorization. Missing key configuration disables writes.
- Rate limiting and bounded balance-cache eviction are not implemented. Use a
  trusted local environment until resource limits are designed and verified.
- RPC URLs may include provider credentials and may appear in error logs. Treat
  logs as sensitive; review/redact them before sharing.
- A PostgreSQL advisory lock serializes competing indexers per chain/database,
  and detected forks latch a persistent recovery-required state exposed by
  `/health`. Automatic chain reorganization rollback, replay and derived-data
  repair are not implemented. See README for correctness limits.

Run locally with disposable credentials and synthetic data. No production
service deployment, external penetration test, live-chain test, load benchmark,
or container vulnerability scan is claimed by this review.

## Reporting

Do not put credentials or exploit details in public issues. Use GitHub private
vulnerability reporting when available; otherwise describe the issue without
sensitive details and request a private channel.
