# Timestamp Service

This directory contains the FastAPI, PostgreSQL, timestamp-worker, notification-worker, proof-bundling, Stripe, public-calendar, Bitcoin Core, and Resend implementation for the optional paid timestamp service. Code completeness alone does not authorize deployment, provider changes, public calendar submission, or payment collection.

The free browser-generated, locally signed COA remains complete and independently verifiable without this service.

## Safety boundary

- Safe defaults are `PAYMENT_MODE=disabled`, `CHECKOUT_ENABLED=false`, `CALENDAR_MODE=disabled`, `BITCOIN_VERIFIER=disabled`, `RESEND_SENDER_MODE=disabled`, and `RESEND_WEBHOOK_MODE=disabled`.
- `PAYMENT_MODE=stripe_live` is supported only when settings validate `APP_ENV=production`, `STRIPE_LIVE_ENABLED=true`, matching live credentials and Price, HTTPS return origins, and non-Phase-0 product and policy versions. A restricted Stripe key must grant Price read access in addition to the permissions used for checkout and webhook reconciliation. New checkout remains unavailable until the independent `CHECKOUT_ENABLED=true` gate is also set. Neither code gate is owner launch approval.
- Fixture payment, calendar, and Bitcoin adapters require `APP_ENV=test` and an active `pytest` process. They are test doubles, not a runnable local service mode.
- Disabled payment mode or `CHECKOUT_ENABLED=false` exposes health and existing-order routes but new checkout is unavailable. Stripe webhooks and paid-order fulfillment remain available when only the checkout gate is off.
- Public `GET /v1/checkout/price` is rate-limited and available only when the active one-time USD Stripe Price exactly matches the configured checkout amount and currency. New checkout repeats that provider/configuration validation before contacting Stripe.
- `CALENDAR_MODE=public` composes `MultiCalendarTimestamper` with `HardenedCalendarTransport`. It requires staging/production and at least two allowlisted HTTPS calendar hosts; the transport rejects unsafe DNS snapshots, pins one vetted public IP per request, preserves hostname TLS/SNI, rejects redirects, and bounds fan-out and responses. Calendar pilot use still requires explicit owner authorization and independent security review.
- `BITCOIN_VERIFIER=bitcoin_core` composes `BitcoinCoreRpcTransport` and `BitcoinCoreRpcVerifier` in staging/production. RPC must be private and authenticated; never expose it publicly.
- `RESEND_SENDER_MODE=resend` enables the separate durable notification worker. `RESEND_WEBHOOK_MODE=resend` enables signed `POST /v1/webhooks/resend` handling.
- `METBULL_LOOKUP_ENABLED=true` enables exact-code lookup from the bundled, read-only Meteoritical Bulletin snapshot. Requests never fetch LPI or any other external service. Only `Official` and `Relict` records are accepted; other catalog statuses return a conflict response.
- A browser return does not authorize fulfillment. Only a verified, canonical payment webhook can do so in Stripe test mode.
- `calendar_pending` and proof availability do not mean Bitcoin-confirmed.
- Ordinary pending confirmation schedules a durable successor check six hours later; it does not exhaust a short retry budget or dead-letter solely because Bitcoin confirmation is still pending.
- Bitcoin verification and downloadable bundle readiness are separate. State can become `bitcoin_verified` with `proof_available=false`; a later durable bundle job creates the matching artifact and an initial notification at `>=1` confirmation. Only a verified Resend `email.delivered` event for that initial notice moves the order to `delivered`. A final notice is enqueued at `>=6` confirmations.
- Lost confirmation, decreased confirmation count, or changed immutable block evidence fails closed through terminal handling to `manual_review`; proof download is suppressed pending audited recovery.
- Every raw `.ots` proof must be between 1 and 262,144 bytes. Proof versions are append-only, the latest valid version is authoritative for cryptographic state, and current state plus matching persisted-bundle readiness controls download eligibility.
- `stamping` suppresses proof availability and calendar/Bitcoin timestamps even if historical rows exist.
- The service receives only the certificate reference, exact manifest SHA-256, fulfillment email, and versioned consent. It must never receive private keys, passphrases, images, manifest contents, the COA ZIP, address/provenance fields, or card data.

The attached `/Users/rbj/Desktop/OpenTimestamps_COA_Methodology.md` is the direct-digest methodology. The managed path decodes the lowercase hexadecimal SHA-256 to the original 32 bytes and constructs the detached proof directly. It does not hash the hexadecimal text or the digest again. Do not edit the attached source as part of this repository.

## Install and validate

Use Python 3.12 exactly:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements.lock
python -m pip check
ruff check app migrations scripts tests
mypy
pytest
```

To include the Postgres migration test, provide an ephemeral test database and run the migration before tests:

```bash
export DATABASE_URL='postgresql+psycopg://timestamp_test:local-password@127.0.0.1:5432/timestamp_test'
export TEST_POSTGRES_URL="$DATABASE_URL"
alembic upgrade head
pytest
```

Use only local disposable credentials in those URLs. CI performs this flow against an ephemeral Postgres service without provider secrets.

## Meteoritical Bulletin snapshot

The exact-code lookup is derived from the official Meteoritical Bulletin Database CSV published by the Lunar and Planetary Institute (LPI): <https://www.lpi.usra.edu/meteor/>. LPI remains the source and authority. The bundled metadata records the fixed export URL, retrieval time, source SHA-256, database SHA-256, and row count. Country is derived only from a reviewed static allowlist of exact `Place` values and well-formed `..., Country` suffixes. Unambiguous aliases such as `USA` are normalized; generic regions (including Antarctica and Northwest Africa), oceans, uncertain values, and unknown spellings remain null. The raw `Place` field is not bundled or exposed.

Updating is a deliberate maintainer operation, not a runtime or scheduled service:

```bash
python scripts/update_metbull_snapshot.py
pytest tests/metbull
```

The updater contacts only the fixed official HTTPS CSV URL, rejects redirects and proxy settings, limits the response to 32 MiB, validates the schema and records, and atomically replaces the validated read-only SQLite snapshot and attribution metadata. Review both generated files and their hashes before release.

## Local disabled startup

Create an untracked local environment file and replace its placeholders with values generated locally. For readiness, uncomment and set `TOKEN_PEPPERS__1` and `ACTIVE_TOKEN_PEPPER_VERSION`; payment remains disabled. Never commit `.env`, and never send its contents through chat, tickets, logs, screenshots, or browser code.

```bash
cp .env.example .env
docker compose up -d postgres
set -a
source .env
set +a
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

With the safe defaults, `GET /health/live` is available, `GET /health/ready` checks Postgres, and checkout fails closed. Apply `alembic upgrade head` before API or worker startup; do not run migrations independently from every replica.

In another shell with the same disabled environment, a one-cycle worker smoke test is valid and performs no calendar or Bitcoin network operation when no job is available:

```bash
set -a
source .env
set +a
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker python -m app.worker.cli --once
```

This smoke test does not exercise checkout, fixture timestamping, public calendars, or confirmation.

## Deterministic fixture tests

Fixture checkout and proof flows are available only through tests, which install `pytest` before constructing the adapters:

```bash
pytest tests/api tests/payments tests/fulfillment tests/workers
```

The fixtures never contact Stripe, public calendars, Bitcoin, or email. Use only synthetic `.test` addresses and fixture digests. A fixture `bitcoin_verified` result is deterministic test evidence, never public Bitcoin evidence. There is no supported direct fixture API/worker startup command.

Stripe test mode requires `APP_ENV=test` or `staging`, `PAYMENT_MODE=stripe_test`, `STRIPE_TEST_ENABLED=true`, test-only Stripe credentials with Price read access, a server-controlled one-time USD Price, and HTTPS return origins. A Stripe-test sandbox was previously deployed and checkout/refund canaries were recorded working, but that recovered state has not been freshly verified. It must not be treated as current provider evidence, enabled in routine local tests/CI, or used with live keys or real charges.

## Commands

API, after loading the disabled environment above:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Worker, one bounded claim cycle in disabled mode:

```bash
TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker python -m app.worker.cli --once
```

Notification worker, only after Resend sender mode is deliberately configured:

```bash
NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker python -m app.notifications.cli --once
```

Operator commands require `DATABASE_URL` and the serialized operator factory:

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.replay_job JOB_UUID --confirm REPLAY:JOB_UUID
python -m scripts.upgrade_order ORDER_UUID --confirm UPGRADE:ORDER_UUID
python -m scripts.reverify_order ORDER_UUID --request-id CHANGE_ID --confirm REVERIFY:ORDER_UUID:CHANGE_ID
```

Inspect the order and job first. Replay accepts an ordinary retry job. For manual-review/dead-letter jobs it validates limited proof invariants and then deliberately refuses recovery because no audited fulfillment-state transition exists. Upgrade accepts only pending orders; reverify accepts only verified or delivered orders. Do not run concurrent operator mutations for the same order.

Reverification records append-only confirmation observations and rejects lost or conflicting immutable block evidence. Scheduled confirmation monitoring rejects lost, decreased, or conflicting evidence through `manual_review`. Live use still requires an owner-approved verifier, confirmation, reorganization, retention, and recovery policy. Do not use manual review as a queue that an operator can force back into fulfillment.

Checkout creation uses committed processing/grace leases. Concurrent retries using the same idempotency key may receive HTTP `425` without a recovery token; wait for the 5-300 second configured grace period (60 seconds by default) before retrying the identical request. Never switch keys to bypass an in-progress lease.

## Deployment boundary

Keep the backend in a private, protected deployment repository or equivalent reviewed source boundary. Neon is the current database target. Migration `20260827_0002` and all backend tests passed against isolated Neon branches on 2026-08-27; production was empty, so customer-data restore and proof reverification remain future gates when applicable. `railway.toml` is optional deployment metadata only; it does not establish Railway as the deployment platform or database provider.

Deployment preparation and every remaining gate are in [`../docs/TIMESTAMP_SERVICE_DEPLOYMENT.md`](../docs/TIMESTAMP_SERVICE_DEPLOYMENT.md). Operational recovery is in [`../docs/TIMESTAMP_SERVICE_OPERATIONS.md`](../docs/TIMESTAMP_SERVICE_OPERATIONS.md).
