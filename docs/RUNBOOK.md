# Local operation and gated testnet pilot

[Project map](INDEX.md)

This is an operator guide, not evidence of an existing deployment. Complete the M1 correctness gates in [ROADMAP.md](ROADMAP.md) before a live pilot. Use only your own test wallets. Public read routes expose wallet addresses and labels; do not ingest customer data.

## Local API

From the repository root, with Docker running:

```sh
cp .env.example .env.local
# Edit .env.local: generate SENTINEL_API_KEY_SALT with openssl rand -hex 32.
# Verify network, token address and START_BLOCK against your chosen provider.
compose() { docker compose --env-file .env.local "$@"; }
compose build
compose up -d --wait postgres
compose run --rm --no-deps api python -m alembic upgrade head
compose up -d api
curl -fsS http://localhost:8000/api/v1/health
# Issue a key; keep the plaintext output in a password manager, not a log.
compose run --rm --no-deps -v "$PWD/scripts:/opt/sentinel-scripts:ro" api \
  python /opt/sentinel-scripts/gen_api_key.py --label local-operator
compose up -d indexer
```

Use `http://localhost:8000/docs` to register a test wallet with the issued `X-API-Key`. Start with a narrow historical range and inspect the ledger. Without active registered wallets the worker advances its checkpoint without reconstructing wallet history. Registering a wallet later does not automatically backfill it. Avoid changing `START_BLOCK` on an existing database: the stored checkpoint wins. A future start leaves the checkpoint absent until the head reaches that block.

The database is available only on localhost:15433; the API on localhost:8000. `compose stop` preserves data. Never use `down -v` on data you intend to keep. The local API's rules runner is in-process; keep one API replica.

## Pilot prerequisites

1. Finish M1 canonical-chain/reorg and wallet-projection gates; document privacy/auth decisions and collect only operator-owned test data.
2. Provision a Linux host with Docker Compose v2, adequate disk space, firewall ports 80/443, DNS and a TLS hostname. No cloud resources are created by this repository.
3. Clone this private repository to `~/arc-sentinel`. Configure read-only repository and private GHCR credentials on the host. Test `git fetch` and `docker pull` as the deploy user without interaction; do not put credentials into URLs or Compose files.
4. Create `.env.production` from `.env.example`, mode 600. Use unique generated database password and salt; hex passwords avoid URL-escaping problems. Set network/token/start deliberately, remove conflicting legacy aliases, and set `ARC_SENTINEL_DOMAIN`. Keep this file and backups outside Git. Provision configuration separately; Actions does not rewrite application secrets.
5. Create GitHub environment `pilot` with any desired environment protection. Configure secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_PRIVATE_KEY`, `DEPLOY_KNOWN_HOSTS`. Verify the SSH host fingerprint through a trusted channel before recording known_hosts; do not blindly trust a runtime scan. Set repository variables `DEPLOY_ENABLED=true` and `ARC_SENTINEL_DOMAIN` only when ready.
6. Run the CI workflow successfully on main and record its full commit SHA and image digest. Manual deployment takes the full 40-character SHA and checks main ancestry. SHA tags can be overwritten: use a digest for direct operator deployments and adopt digest verification in Actions before stronger supply-chain claims.

The Deploy pilot workflow runs only from main and requires explicit dispatch. It pulls the selected image, starts PostgreSQL, stops API/indexer, runs Alembic, starts services, and checks `/api/v1/health`. This incurs downtime. It does not prove a selected SHA passed CI: the operator must check that evidence. Workflow health failure does not trigger rollback. The scheduled health workflow is an availability check, not an alert delivery or incident-management system.

For a manual host operation:

```sh
cd ~/arc-sentinel
export SENTINEL_IMAGE=ghcr.io/chuliyu/arc-sentinel:<tested-sha-or-digest>
compose() { docker compose --env-file .env.production -f docker-compose.prod.yml "$@"; }
compose config --quiet
compose pull
compose up -d --wait postgres
# Take and verify a backup before any schema migration (see below).
compose stop api indexer
compose run --rm --no-deps api python -m alembic upgrade head
compose up -d
```

Never print `compose config` or application settings in shared logs: they contain credentials. Apply migration `0003` before starting this revision. Health remains HTTP 200: inspect its body `status` and `indexer.recovery_required`, reason, common ancestor and detection time. A latched fork/legacy checkpoint stays degraded even near the head or with RPC unavailable. Null recovery state does not prove a worker is running: other retryable failures still require logs. Check checkpoint lag, recent block/transaction samples, worker errors, recovery after restart, disk space, and rule outcomes as well.

## Failure and replay

- **Wrong chain:** leave the checkpoint untouched, correct RPC/network settings, restart the worker. Never fix this by editing a checkpoint.
- **Missing block/receipt or malformed log:** the batch rolls back. Inspect provider errors and block identifiers without logging RPC secrets. Repair the provider/decoder and restart; the same range is retried. Never advance the checkpoint to bypass it.
- **Indexer busy:** another transaction holds the writer lock. Verify the legitimate worker; investigate a long-running poll before stopping a duplicate. The database releases the lock on transaction end/disconnect.
- **Forked checkpoint:** the worker searches up to `REORG_MAX_DEPTH` preceding headers (default 64) and latches `checkpoint_fork` with a common ancestor, or `rollback_window_exceeded`, `history_boundary` or `journal_gap` with no ancestor. The checkpoint, journal and raw rows are preserved; ledger reads still expose that unrepaired view. Restarting, changing START_BLOCK or returning to an old provider cannot clear the latch. Stop indexing, preserve a database snapshot and record affected hashes. Automatic rollback/reset is not implemented; use the separate-database rebuild/reconciliation procedure below. Never clear diagnostics, edit the checkpoint/journal, or downgrade to bypass recovery.
- **Parent/hash mismatch during a new batch or ancestor search:** inconsistent provider responses roll back without a recovery latch. Inspect logs and provider stability; a successful next poll must pass all checks. A transient mismatch is not a verified recovery plan.
- **Provider switched during a cycle:** the entire batch is retried. A subsequent stable fallback cycle may succeed after chain/header checks. Repeated failures require provider investigation, not bypassing checks.
- **Legacy height-only checkpoint after revision `0002`:** schema upgrade preserves the data but the worker stops with a recovery-required error. Keep the original database intact. For an operator-owned demo, prepare a separate empty database, register the intended wallets before starting, choose an explicit historical start and rebuild there; compare the chosen reference range before a deliberate configuration cutover. Preserve original registrations/labels and securely reissue credentials as needed. This is an operator migration, not an automatic recovery tool. A zero-loss in-place historical reconciliation remains M1 work; do not fabricate hashes for the existing checkpoint.
- **Rules or API failure:** keep a single API replica and inspect stored alerts. Indexer atomicity does not provide exactly-once rule evaluation.

## Backup, restore and rollback

Before migration, from the host shell using the pilot `compose` function:

```sh
set -o pipefail
umask 077
mkdir -p backups
# Choose a fresh filename. Copy encrypted backups off-host under a retention policy.
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' \
  | gzip > backups/pre-migration.sql.gz
# Enable pipefail in your shell so a failed dump does not look successful.
gzip -t backups/pre-migration.sql.gz
```

A compressed file passing `gzip -t` is not a restore drill. Restore into a **separate disposable database/container**, with `psql --set ON_ERROR_STOP=1`, compare ledger/checkpoint counts, run representative reads, and record duration before setting RPO/RTO claims. The older `scripts/backup.sh`/`restore.sh` require an explicit Compose-generated container name and are not used by deployment automation.

For an application-only rollback, stop API/indexer, select a previously verified image and compatible configuration revision, and restart. If schema compatibility is uncertain, leave services stopped and restore a validated backup into a separate database first. Do not blindly downgrade Alembic or import a dump into the populated live database. Record the incident, checkpoint, image digest, backup identity and reconciliation result. Off-host backups, restore drill, metrics and notification routing remain pilot gates, not implemented services.
