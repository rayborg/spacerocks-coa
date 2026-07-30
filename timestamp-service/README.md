# Timestamp Service

This directory contains the Phase 0 FastAPI, Postgres, durable-worker, and proof-bundling implementation for the optional managed timestamp service. It is sandbox-only. It does not authorize deployment, real payment collection, public calendar submission, or a Bitcoin-confirmed claim.

The free browser-generated, locally signed COA remains complete and independently verifiable without this service.

## Safety boundary

- `PAYMENT_MODE=stripe_live` is rejected.
- Fixture payment, calendar, and Bitcoin adapters require `APP_ENV=test` and an active `pytest` process. They are test doubles, not a runnable local service mode.
- Disabled payment mode exposes health routes but checkout is unavailable.
- Public calendar parsing/fan-out code is not a usable runtime transport: settings/composition expose no public mode, and the default transport refuses operation pending pinned-public-IP TLS/SNI review. No production Bitcoin verification source or email sender is implemented.
- A browser return does not authorize fulfillment. Only a verified, canonical payment webhook can do so in Stripe test mode.
- `calendar_pending` and proof availability do not mean Bitcoin-confirmed.
- Ordinary pending confirmation schedules a durable successor check six hours later; it does not exhaust a short retry budget or dead-letter solely because Bitcoin confirmation is still pending.
- Bitcoin verification and downloadable bundle readiness are separate. State can become `bitcoin_verified` with `proof_available=false`; a later durable bundle job makes the matching artifact available without changing fulfillment state. Without a sender, the order remains `bitcoin_verified`; `delivered` is reserved for a future audited sender transition.
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

Stripe test mode exists for a later, separately authorized sandbox exercise. It requires `APP_ENV=test` or `staging`, `PAYMENT_MODE=stripe_test`, the explicit `STRIPE_TEST_ENABLED=true` gate, test-only Stripe credentials, a server-controlled test Price, and HTTPS return origins. It must not be enabled in routine local tests or CI, and it must never use live keys or real charges.

## Commands

API, after loading the disabled environment above:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Worker, one bounded claim cycle in disabled mode:

```bash
TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker python -m app.worker.cli --once
```

Operator commands require `DATABASE_URL` and the serialized operator factory:

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.replay_job JOB_UUID --confirm REPLAY:JOB_UUID
python -m scripts.upgrade_order ORDER_UUID --confirm UPGRADE:ORDER_UUID
python -m scripts.reverify_order ORDER_UUID --request-id CHANGE_ID --confirm REVERIFY:ORDER_UUID:CHANGE_ID
```

Inspect the order and job first. Replay accepts an ordinary retry job. For manual-review/dead-letter jobs it validates limited proof invariants and then deliberately refuses recovery because no audited fulfillment-state transition exists. Upgrade accepts only pending orders; reverify accepts only verified or delivered orders. Do not run concurrent operator mutations for the same order.

Current successful reverification checks immutable block metadata and appends state evidence, but live reverification remains blocked until every run has a typed, append-only history record and an approved verifier, confirmation, and Bitcoin reorganization policy. Do not use manual review as a queue that an operator can force back into fulfillment.

Checkout creation uses committed processing/grace leases. Concurrent retries using the same idempotency key may receive HTTP `425` without a recovery token; wait for the 5-300 second configured grace period (60 seconds by default) before retrying the identical request. Never switch keys to bypass an in-progress lease.

## Repository extraction gate

Before authorizing Railway's GitHub App, extract this backend into a separate private backend repository with its contracts, tests, migration, lock file, Dockerfile, and runbooks. Preserve history or record a reviewed source snapshot, update contract paths deliberately, rerun all CI, and protect the default branch. Grant the GitHub App access only to that backend repository. Do not authorize Railway against this combined frontend repository as a shortcut.

Deployment preparation and every remaining gate are in [`../docs/TIMESTAMP_SERVICE_DEPLOYMENT.md`](../docs/TIMESTAMP_SERVICE_DEPLOYMENT.md). Operational recovery is in [`../docs/TIMESTAMP_SERVICE_OPERATIONS.md`](../docs/TIMESTAMP_SERVICE_OPERATIONS.md).
