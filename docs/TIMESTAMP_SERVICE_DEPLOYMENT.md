# Timestamp Service Deployment Preparation

## No-deploy status

This is a preparation guide, not deployment authorization. Phase 0 is sandbox-only. The application rejects `stripe_live`. Public calendar parsing/fan-out cannot be enabled through settings/composition, and its default transport intentionally refuses operation until pinned-public-IP TLS/SNI transport is implemented and independently reviewed. Production Bitcoin verification and email sending are absent. No Railway project, provider account, public endpoint, or live service should be created from these instructions until the account owner completes the applicable plan gates and explicitly authorizes the next phase.

## Repository and GitHub boundary

Before granting Railway access:

1. Extract `timestamp-service` into a separate private backend repository and include reviewed contracts, tests, migrations, pinned dependency lock, Dockerfile, deployment metadata, and runbooks.
2. Preserve history or record the exact reviewed source revision. Adjust contract test paths deliberately and rerun all CI after extraction.
3. Protect the default branch, require pull-request review and CI, enable secret scanning/dependency alerts as available, require MFA, and restrict collaborators.
4. Install Railway's GitHub App only on the backend repository. Do not grant organization-wide or combined frontend-repository access, and do not use a personal access token for routine deploys.
5. Keep CI non-deploying with `contents: read`, no provider secrets, and no `pull_request_target` execution of untrusted code.

## Environment isolation

Use distinct Railway projects or strongly isolated environments for staging and any future production. They require separate Postgres databases, domains, Stripe objects/keys/webhook secrets, token peppers, logs, backups, budgets, operators, and frontend API URLs. Never clone production customer data into staging. Production must remain `PAYMENT_MODE=disabled` in Phase 0 because live mode is unsupported.

Staging is not safe merely because Stripe is in test mode: its email, digest, tokens, and proof records are still sensitive. Use synthetic records until privacy, retention, and deletion policies are approved.

## Required variables

All backend values belong in Railway Variables or an approved secret manager, never frontend `VITE_*` configuration. Secret values must be generated and entered directly by an authorized operator; do not transmit them through chat, Git, screenshots, logs, or support tickets.

| Variable | Required condition | Secret | Phase 0 rule |
| --- | --- | ---: | --- |
| `APP_ENV` | Always | No | `staging` for provider sandbox; `production` cannot enable payments in Phase 0 |
| `PAYMENT_MODE` | Always | No | `disabled` by default; `stripe_test` only for authorized sandbox; never `stripe_live` |
| `CALENDAR_MODE` | Always | No | `disabled`; fixture requires `pytest`; there is no public runtime value |
| `BITCOIN_VERIFIER` | Always | No | `disabled`; fixture requires `pytest`; no production source exists |
| `DATABASE_URL` | Staging/production and any enabled service | Yes | Railway private Postgres reference using PostgreSQL |
| `ALLOWED_ORIGINS` | Browser access | No | JSON list of exact HTTPS frontend origins; no wildcard, credentials, path, query, or fragment |
| `TOKEN_PEPPERS__N` | Status/proof service | Yes | At least 32 random bytes for each retained positive version |
| `ACTIVE_TOKEN_PEPPER_VERSION` | Status/proof service | No | Must name a configured pepper version |
| `TOKEN_TTL_SECONDS` | Optional override | No | 300 through 7,776,000; requires approved retention/access policy |
| `CHECKOUT_CREDENTIAL_GRACE_SECONDS` | Optional override | No | 5-300; default 60; protects provider processing/token replay leases |
| `STRIPE_TEST_ENABLED` | Stripe sandbox only | No | Must be `true` with `stripe_test`; explicit kill gate |
| `STRIPE_SECRET_KEY` | Stripe sandbox only | Yes | Least-privilege `sk_test_...` credential only |
| `STRIPE_WEBHOOK_SECRET` | Stripe sandbox only | Yes | Secret for this exact staging endpoint only |
| `STRIPE_PRICE_ID` | Stripe sandbox only | No, sensitive config | Server-controlled sandbox Price ID |
| `CHECKOUT_AMOUNT_MINOR` | Enabled checkout | No | Approved positive test amount; frontend cannot set it |
| `CHECKOUT_CURRENCY` | Enabled checkout | No | Approved lowercase three-letter currency |
| `PRODUCT_VERSION` | Enabled checkout | No | Immutable reviewed product/policy version, 1-64 characters |
| `CHECKOUT_SUCCESS_URL` | Stripe sandbox only | No | HTTPS URL whose origin appears in `ALLOWED_ORIGINS` |
| `CHECKOUT_CANCEL_URL` | Stripe sandbox only | No | HTTPS URL whose origin appears in `ALLOWED_ORIGINS` |
| `STRIPE_SIGNATURE_TOLERANCE_SECONDS` | Optional override | No | 60-600; default 300 |
| `STRIPE_API_TIMEOUT_SECONDS` | Optional override | No | 1-30 seconds; default 10 |
| `TRUSTED_PROXY_IPS` | Forwarded client IP use | No | JSON list of verified immediate proxy IPs only; empty is safer than broad trust |
| `CHECKOUT_RATE_LIMIT` | Optional override | No | Positive requests/minute policy |
| `WEBHOOK_RATE_LIMIT` | Optional override | No | Positive requests/minute policy |
| `STATUS_RATE_LIMIT` | Optional override | No | Positive requests/minute policy |
| `PROOF_RATE_LIMIT` | Optional override | No | Positive requests/minute policy |
| `ROTATION_RATE_LIMIT` | Optional override | No | Positive requests/minute policy |
| `TIMESTAMP_WORKER_FACTORY` | Worker service | No | Exactly `app.worker.composition:create_worker` |
| `TIMESTAMP_WORKER_ID` | Worker service | No | Unique opaque instance ID if set |
| `TIMESTAMP_OPERATOR_FACTORY` | One-off operator process only | No | Exactly `app.worker.operator:create_operator_commands` |

The only frontend variable is public `VITE_TIMESTAMP_API_URL`, set to the reviewed HTTPS API base path. Leaving it absent removes the paid UI and timestamp requests. Do not put a token, key, email, database URL, or provider secret in any `VITE_*` value.

No email variables are defined because no sender is implemented. Bundle/outbox creation leaves the order at `bitcoin_verified`; current runtime never transitions to `delivered`. Do not invent provider variables or mark delivery complete until a separately reviewed adapter, minimized templates, sender-domain controls, bounce handling, and an audited delivery transition exist.

There is likewise no calendar URL/enable variable. `MultiCalendarTimestamper` is not reachable from runtime composition, and direct construction without an injected reviewed transport fails closed. Do not add undocumented variables or bypass composition to activate it.

## Railway services

Create separate services from one reviewed immutable image:

- API: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --limit-concurrency 100 --timeout-keep-alive 5 --no-access-log`
- Worker: `TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker python -m app.worker.cli`
- Migration: serialized one-off `alembic upgrade head` before the API/worker revision starts
- Postgres: private Railway Postgres with monitored backups, capacity alerts, restricted access, and tested off-provider restore

Do not run the migration from every replica. The worker must not share the public API domain. Operator scripts run only as controlled one-off tasks, never as a public service. In the current disabled deployment posture, a worker `--once` with no jobs is only a smoke test; it cannot perform checkout fulfillment. Do not create paid/queued sandbox orders while calendar and verifier composition remain disabled, because attempted work will safely fail and can move to manual review.

The Docker image runs as UID/GID `10001:10001`, writes temporary data only under `/tmp`, and needs no persistent application filesystem because proofs are stored in Postgres. Enforce a read-only root filesystem where the platform supports it, retain a small `noexec,nosuid` temporary filesystem, drop Linux capabilities, and do not mount Docker sockets, source credentials, or writable source volumes. If Railway cannot enforce the reviewed container restrictions, record and approve the compensating control before staging.

## Network, CORS, origin, and proxy

- Expose only the API HTTPS domain. Keep Postgres on Railway's private network.
- Set `ALLOWED_ORIGINS` to exact deployed HTTPS frontend origins. CORS is browser policy, not authentication.
- Preserve `Authorization`, `Idempotency-Key`, `Content-Type`, and `Stripe-Signature` headers through the proxy. Never log their values or request bodies.
- Verify Railway's actual immediate proxy addressing before setting `TRUSTED_PROXY_IPS`. The application trusts the first `X-Forwarded-For` value only when the direct peer is allowlisted; never use `0.0.0.0/0`, `*`, or guessed ranges.
- Keep API docs disabled outside local/test through `APP_ENV`. Retain response security headers, request-size limits, and `no-store` behavior.
- Put edge rate/abuse controls in front of application limits and ensure they do not depend on spoofable forwarded headers.

## Health and startup

Railway's API health path is `/health/ready`. `/health/live` indicates process response only; `/health/ready` additionally checks the configured database store. Neither endpoint proves worker, Stripe, calendar, Bitcoin verification, email, backup, or restore health.

Deployment order:

1. Build and scan the reviewed image without embedding variables.
2. Back up Postgres and verify the expected current migration revision.
3. Pause checkout and worker mutations if compatibility requires it.
4. Run one migration task and verify `alembic current`.
5. Deploy API, wait for readiness, then deploy worker.
6. Exercise liveness, readiness, CORS rejection/allowlisting, rate limits, and synthetic authenticated status.
7. Exercise Stripe behavior in automated mocked/fixture tests. A provider-backed Stripe sandbox checkout remains blocked until there is an approved fulfillment test plan that cannot strand paid/queued work while public transport and production verification are unavailable.
8. Resume checkout only after monitoring is active and rollback compatibility is confirmed.

## Stripe sandbox webhook

When a later provider-backed staging exercise is explicitly authorized, register a staging-only HTTPS endpoint at `/v1/webhooks/stripe`. Use a separate sandbox endpoint secret and subscribe only to reviewed event types. The raw body must reach FastAPI unchanged. A `2xx` means required state was durably persisted, not that timestamp work completed. Verify duplicate, stale, invalid-signature, wrong-mode, wrong-amount/currency/Price, out-of-order, refund, dispute, expiration, and delayed-event cases before broader sandbox use.

The success/cancel browser URLs are status navigation only. They never authorize fulfillment or prove payment. No live webhook, key, Price, or payment mode is permitted in Phase 0.

Checkout calls use a committed processing/grace lease. Concurrent identical requests may receive HTTP `425` with no token while the 5-300 second lease is active. Clients must wait and retry the identical body and idempotency key; changing keys to bypass the lease is prohibited. Monitor lease age and repeated `425` responses.

## Proof artifacts and current state

Every raw `.ots` proof is limited to 262,144 bytes by parser/domain/database/contract checks. Proof versions are append-only and retain digest, checksum, length, state, and original calendar-submission time. The highest valid version is the current cryptographic artifact; historical durability never authorizes projection of stale state.

`stamping` suppresses proof availability and both calendar/Bitcoin timestamps. Pending downloads are built from the selected latest version and returned only after a second transaction confirms the same token, immutable order binding, `calendar_pending` state, and unchanged latest metadata. Verification may set `bitcoin_verified` before the separate bundle job persists its output, so status can legitimately report verified with `proof_available=false`. Verified download readiness becomes true only when the persisted bundle is bound to the current verified proof version and has matching length/checksum. `manual_review` suppresses proof availability/download even when historical proof, verification, or bundle rows remain. Backup, restore, replication, caching, and monitoring designs must preserve these rules.

Calendar submission is not transactional with Postgres. A crash after calendar acceptance but before proof append can cause at-least-once resubmission of the same immutable digest. This is an accepted recovery ambiguity, not exactly-once behavior and not a unique-transaction guarantee.

## Monitoring and rollback

Before provider-backed staging, configure alerts listed in the [operations runbook](TIMESTAMP_SERVICE_OPERATIONS.md), including readiness, checkout lease/`425` age, paid-to-stamped age, six-hour pending-successor gaps, pending age, error retries, target mismatch, webhook failures, Postgres capacity, backup failure, restore drill, and budget thresholds. Ordinary pending schedules six-hour successor jobs and must not dead-letter merely due to a short retry window. Polling/retention/escalation maximums remain approval gates. Logs and traces must exclude tokens, emails, request bodies, Stripe signatures/secrets, database URLs, and proof bytes.

Rollback by immutable image digest only when the prior image is schema-compatible. Pause checkout, serialize worker transition, preserve all order/event/proof history, and avoid database downgrade unless a reviewed migration plan requires it. Calendar commitments already submitted are irreversible and survive rollback, refund, or database restoration.

## Live gates

Live deployment remains blocked until every applicable plan gate is evidenced, including:

- live payment implementation and security review rather than bypassing the current rejection;
- account-owner Stripe activation, approved business details, least-privilege live credentials, separate webhook, price, tax/accounting/legal review, and tested refund/dispute procedures;
- approved terms, privacy, retention/deletion, support, delivery timing, claims, calendar-delay, and service-failure policies;
- pinned-public-IP TLS/SNI public-calendar transport, independent security review, explicit settings/composition integration, multi-calendar policy, and end-to-end pilot using synthetic random digests;
- documented at-least-once same-digest crash/resubmission behavior and reconciliation without an exactly-once or unique-transaction claim;
- approved Bitcoin verification source, exact-target verification, confirmation and reorganization policy, plus typed append-only history for every repeated reverification;
- approved six-hour pending-poll duration, retention, customer-escalation, and eventual-disposition policy;
- approved email provider/domain, minimal templates, delivery/bounce/complaint handling, secret rotation, and audited `bitcoin_verified` to `delivered` transition;
- monitored backups, encrypted off-provider copy, successful clean restore and proof reverification drill;
- staging/production isolation, least-privilege access, MFA/recovery controls, incident exercises, capacity/cost controls, and rollback drill; and
- explicit account-owner authorization after sandbox acceptance review.

Never call a proof confirmed while it is pending or because checkout redirected successfully. Never describe the complete COA, certificate contents, photographs, or PII as stored on Bitcoin.
