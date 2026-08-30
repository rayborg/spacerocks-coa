# Timestamp Service Operations

## Status and authority

This runbook covers implemented MVP operations. It is not authorization to alter Neon, Stripe, Resend, DNS, deployment infrastructure, Bitcoin Core, public calendars, payments, or production. Production is not publicly live and real customer payments are unavailable.

A Stripe-test sandbox was previously deployed and checkout/refund canaries were recorded working. That recovered state has not been freshly verified; confirm every current environment fact before relying on it. Never invent missing cloud state or URLs.

Only authorized operators may run migrations, restore data, mutate jobs, rotate credentials, enable modes, submit a calendar digest, refund a payment, or communicate with customers. Secrets go directly into approved secret managers/provider consoles, never chat, Git, commands, logs, tickets, screenshots, or frontend variables.

## Preflight

Before any operation:

1. Record the environment, immutable deployment revision, non-sensitive change/incident ID, and responsible operator.
2. Confirm the actual Neon database target, schema revision, API revision, payment/calendar/Bitcoin/Resend modes, and worker revisions.
3. Confirm production public access and real payments remain disabled unless the owner has explicitly authorized the exact operation.
4. Pause conflicting deploys and assign one operator to each mutable order/job/provider action.
5. Verify current backups and perform database-affecting validation on an isolated restore first.
6. Confirm logs and evidence omit bearer tokens, emails, digests linked to PII, request bodies, provider signatures, secrets, RPC credentials, and database URLs.

Safe mode defaults are `PAYMENT_MODE=disabled`, `CALENDAR_MODE=disabled`, `BITCOIN_VERIFIER=disabled`, `RESEND_SENDER_MODE=disabled`, and `RESEND_WEBHOOK_MODE=disabled`.

## Migration and Neon restore gate

Neon is the current PostgreSQL target. Migration `20260827_0002` adds append-only Bitcoin confirmation observations, durable notification attempts, Resend webhook events, and delivery-evidence constraints.

Run one serialized migration task, never one per replica:

```bash
cd timestamp-service
alembic current
alembic upgrade head
alembic current
```

The final revision must be `20260827_0002`. `DATABASE_URL` must come from the secret store, not the command line.

Before provider-backed or live use:

- restore a current Neon backup into a clean isolated target;
- disable checkout, API provider modes, both workers, webhooks, and all provider egress on the restore;
- apply/verify the migration there first;
- compare row counts and immutable order/certificate/digest/payment bindings;
- verify every proof length/checksum/target/version and matching bundle;
- verify Bitcoin observation monotonicity and binding to stored verification metadata;
- verify outbox, attempt, provider acceptance, and Resend webhook evidence;
- reverify restored proofs under the approved private verifier/reorganization policy; and
- record recovery-point/time results without customer data or secrets.

Never restore over the source database. If migration or restore validation fails, leave the new revision and payment path stopped; preserve evidence and do not improvise destructive downgrade or row edits.

## Process commands

Timestamp worker:

```bash
export TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker
python -m app.worker.cli --once
```

Notification worker:

```bash
export NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker
python -m app.notifications.cli --once
```

Continuous processes omit `--once`. Optional instance identities are `TIMESTAMP_WORKER_ID` and `NOTIFICATION_WORKER_ID`. IDs must be opaque; do not include hostnames, emails, credentials, or customer data.

Operator commands:

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.replay_job JOB_UUID --confirm REPLAY:JOB_UUID
python -m scripts.upgrade_order ORDER_UUID --confirm UPGRADE:ORDER_UUID
python -m scripts.reverify_order ORDER_UUID --request-id CHANGE_ID --confirm REVERIFY:ORDER_UUID:CHANGE_ID
```

Inspect state, immutable binding, attempts, lease, proof versions, observations, and safe error first. Replay only an ordinary retry with no active lease. Upgrade only `calendar_pending`. Reverify only `bitcoin_verified` or `delivered`. Manual review is a refusal state: current operator code validates limited invariants and then refuses terminal recovery; never edit the state directly or replay around that guard.

## Timestamp lifecycle

- Calendar submission uses `CALENDAR_MODE=public`, `MultiCalendarTimestamper`, and `HardenedCalendarTransport` only after allowlist review and explicit pilot/production authorization.
- At least two independent HTTPS calendar hosts must be configured. A successful initial stamp can include one or more responding calendars.
- Calendar acceptance cannot transact with PostgreSQL proof append. A crash in between may cause at-least-once resubmission of the same immutable 32-byte digest.
- The initial pending proof is append-only. Upgrades append new proof versions and retain the original submission time.
- Ordinary Bitcoin pending schedules another durable upgrade in six hours; it is not an error attempt or short dead-letter path.
- `BITCOIN_VERIFIER=bitcoin_core` requires a private authenticated synchronized mainnet node. RPC must never be public.
- Initial verification requires at least one confirmation and exact digest/block evidence. It records an append-only observation and moves to `bitcoin_verified`.
- The delivery job persists the matching bundle, enqueues the initial `bitcoin-confirmed-initial` notice at `>=1`, and starts 15-minute confirmation monitoring.
- Monitoring enqueues `bitcoin-confirmed-final` at `>=6`.
- A missing attestation after prior verification, decreased confirmation count, or changed block hash/height/time/method/policy is unsafe reorganization/conflict evidence. Terminal handling moves the order to `manual_review`; proof download remains suppressed pending reviewed recovery.

Never claim exactly-once calendar submission, a unique Bitcoin transaction, or Bitcoin confirmation based on payment, redirect, calendar response, fixture result, proof possession, or email.

## Resend delivery

`RESEND_SENDER_MODE=resend` enables `ResendEmailSender` in the separate notification worker. `RESEND_WEBHOOK_MODE=resend` enables `POST /v1/webhooks/resend`. They are independent gates.

The notification worker leases durable outbox records, sends fixed text templates with only the order reference, uses a stable provider idempotency key, records provider acceptance, retries retryable failures with bounded exponential delay, and records terminal/dead-letter outcomes. Never include a bearer token, digest, certificate data, or proof bytes in email.

Resend API acceptance is not delivery. The webhook verifies `svix-id`, `svix-timestamp`, `svix-signature`, raw-body HMAC, timestamp tolerance, event/payload bounds, and event idempotency. Configure and test:

- `email.delivered`;
- `email.bounced`;
- `email.failed`;
- `email.complained`;
- duplicate and conflicting event IDs;
- stale/invalid signatures and oversized/malformed bodies; and
- delivery arriving before local provider acceptance.

Only matching delivery evidence for the initial notice at `>=1`, current proof/bundle, current confirmation observation, and immutable verification can move `bitcoin_verified` to `delivered`. A final notice requires `>=6`; its delivery records evidence but does not change fulfillment state again.

Before enabling sender/webhook modes, verify the approved sending domain and SPF, DKIM, Return-Path, DMARC, sender address, webhook secret, bounce/complaint handling, quotas, and monitored support procedure. These external gates are currently incomplete.

## Durable proof and download rules

- Raw `.ots` proofs contain 1-262,144 bytes.
- Proof versions are append-only with target digest, checksum, length, state, and original calendar time.
- `stamping` reports no proof or calendar/Bitcoin time, even if historical rows exist.
- `calendar_pending` may expose the latest pending proof after a second token/state/version check; it is not Bitcoin-confirmed.
- `bitcoin_verified` can temporarily have `proof_available=false` until its matching persisted bundle passes length/checksum checks.
- `delivered` means the initial Resend message has matching verified `email.delivered` evidence; it does not strengthen Bitcoin evidence.
- `manual_review` reports no proof/download. Historical rows remain evidence, not authorization.

## Pause and recovery

To pause new checkout:

1. Deploy `CHECKOUT_ENABLED=false`; this rejects new sessions before reservation or provider calls while retaining health, authenticated recovery, Stripe webhooks, and paid-order fulfillment.
2. Block checkout at the edge if application rollout is unavailable or immediate containment is required.
3. Use `PAYMENT_MODE=disabled` and remove Stripe settings only when Stripe webhook processing and paid-order reconciliation are intentionally being stopped too.
4. Decide and record whether already-paid timestamp jobs, confirmation monitoring, and notification delivery continue.
5. Reconcile every open, processing, paid, queued, leased, accepted, delivered, refund, and dispute record.
6. Do not assume existing Stripe sessions were canceled; expire/reconcile them through an authorized environment-specific procedure.

Calendar commitments already accepted cannot be canceled. Stopping workers only prevents new external work.

Checkout idempotency uses a 5-300 second processing/grace lease (default 60). A concurrent identical retry may receive HTTP `425` without a token. Wait and retry the identical body and idempotency key; never change keys to bypass the lease.

## Token and secret rotation

Customer tokens travel only in `Authorization`. Use authenticated `POST /v1/orders/rotate-token`; the old token is atomically revoked and the replacement is returned once in the body. Support must never request a token by email/chat.

For pepper rotation, add a new strong `TOKEN_PEPPERS__N`, retain old versions still needed, update `ACTIVE_TOKEN_PEPPER_VERSION`, deploy all consumers consistently, rotate/expire dependent customer tokens, then remove an old pepper only when no valid token needs it.

Rotate Stripe keys/webhook secrets, Resend key/webhook secret, Neon credentials, Bitcoin RPC credentials, deployment access, and GitHub App access one class at a time under an approved plan. Pause the corresponding path, validate with synthetic evidence, revoke the old credential, and reconcile. Never place service secrets in `VITE_*`.

## Incident response

Critical examples include secret/PII exposure, unauthorized fulfillment, wrong-digest proof, false confirmation, unsafe reorganization handling, database compromise, or destructive loss. High examples include unqueued paid orders, webhook bypass, repeat corruption, token disclosure, missing backup, or initial delivery without matching evidence.

1. Declare an incident and assign an owner.
2. Pause checkout and affected timestamp/notification workers; revoke affected access.
3. Preserve append-only payment, proof, observation, job, consent, notification, refund, and dispute evidence.
4. Scope by opaque IDs without exporting unnecessary PII.
5. Restore only into isolation; reverify immutable bindings and proof/notification state before returning service.
6. Communicate only verified facts. Pending remains pending; `delivered` is email-delivery state, not stronger Bitcoin confirmation.
7. Obtain account-owner approval for customer/provider/legal communication and any changed launch gate.

## Monitoring and rollback

Monitor API health/error/latency, Neon readiness/connections/storage/backups, migration revision, checkout `425` age, Stripe webhook failures/reconciliation, timestamp job leases/retries/manual review, six-hour upgrade gaps, 15-minute confirmation gaps, paid-to-stamped and pending age, Bitcoin Core sync/RPC errors, confirmation decreases/conflicts, notification leases/retries/dead letters, Resend acceptance/delivery/bounce/complaint, DNS/TLS, restore drills, costs, refunds, and disputes.

`/health/live` proves only API process response. `/health/ready` proves a configured database query. Neither proves workers or providers.

Rollback only to a schema-compatible immutable revision. Pause checkout, drain/stop workers, preserve all durable evidence, and prefer forward repair over database downgrade. Refund, dispute, rollback, restore, token revocation, or account closure cannot erase a public OpenTimestamps/Bitcoin commitment.

## Neon validation record

On 2026-08-27, two expiring, non-primary Neon branches were cloned from production at the same parent LSN. Production remained idle with zero reported writes. The test branch reached `20260827_0002` and passed all 270 backend tests, including online upgrade/downgrade, trigger, reconciliation, and concurrency coverage. A separate clean branch started with zero application tables, reached head with 16 tables and eight application triggers, and retained zero business/evidence rows. Both validation computes were suspended and the branches were configured to expire on 2026-08-28.

This validates migration mechanics against the current empty production source. It does not validate customer-data recovery, proof checksum comparison, or customer-proof reverification because production contained no application data. Continue backup monitoring and repeat the isolated restore drill when representative data exists.

## Remaining operational gates

Neon backup monitoring and a future customer-data restore drill, Resend domain/DNS/webhook, private deployment controls, calendar pilot approval, private Bitcoin Core checks and reorganization policy, fresh Stripe-test canaries, Stripe tax/live webhook, production DNS/TLS, owner live canaries, policies, monitoring, and explicit launch approval remain incomplete. Keep corresponding modes disabled until the owner authorizes and evidence records each gate.
