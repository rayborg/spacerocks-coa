# Timestamp Service Operations

## Status and authority

This runbook covers implemented MVP operations. It is not authorization to alter Neon, Stripe, Resend, DNS, deployment infrastructure, Bitcoin Core, public calendars, payments, or production. Production is not publicly live and real customer payments are unavailable.

A Stripe-test sandbox was previously deployed and checkout/refund canaries were recorded working. That recovered state has not been freshly verified; confirm every current environment fact before relying on it. Never invent missing cloud state or URLs.

Only authorized operators may run migrations, restore data, mutate jobs, rotate credentials, enable modes, submit a calendar digest, refund a payment, or communicate with customers. Secrets go directly into approved secret managers/provider consoles, never chat, Git, commands, logs, tickets, screenshots, or frontend variables.

## Preflight

Before any operation:

1. Record the environment, immutable deployment revision, non-sensitive change/incident ID, and responsible operator.
2. Confirm the actual Neon database target, schema revision, API/task-service revisions, payment/calendar/Bitcoin/Resend/task-dispatch modes, queue state/settings, and notification-worker revision.
3. Confirm production public access and real payments remain disabled unless the owner has explicitly authorized the exact operation.
4. Pause conflicting deploys and assign one operator to each mutable order/job/provider action.
5. Verify current backups and perform database-affecting validation on an isolated restore first.
6. Confirm logs and evidence omit bearer tokens, emails, digests linked to PII, request bodies, provider signatures, secrets, RPC credentials, and database URLs.

Safe mode defaults are `PAYMENT_MODE=disabled`, `CALENDAR_MODE=disabled`, `BITCOIN_VERIFIER=disabled`, `RESEND_SENDER_MODE=disabled`, and `RESEND_WEBHOOK_MODE=disabled`.

## Migration and Neon restore gate

Neon is the current PostgreSQL target. Migration `20260831_0003` depends on `20260827_0002` and adds exact-generation durable jobs plus Cloud Tasks dispatch intents. The generation is the execution fence: a delayed task for an older generation cannot claim the current job.

Run one serialized migration task, never one per replica:

```bash
cd timestamp-service
alembic current
alembic upgrade head
alembic current
```

The final revision must be `20260831_0003`. `DATABASE_URL` must come from the secret store, not the command line.

Before provider-backed or live use:

- restore a current Neon backup into a clean isolated target;
- disable checkout, API provider modes, the private task service/queue, notification worker, webhooks, and all provider egress on the restore;
- apply/verify the migration there first;
- compare row counts and immutable order/certificate/digest/payment bindings;
- verify every proof length/checksum/target/version and matching bundle;
- verify Bitcoin observation monotonicity and binding to stored verification metadata;
- verify job generations, one dispatch intent per job/generation, deterministic task names, outbox, attempt, provider acceptance, and Resend webhook evidence;
- reverify restored proofs under the approved private verifier/reorganization policy; and
- record recovery-point/time results without customer data or secrets.

Never restore over the source database. If migration or restore validation fails, leave the new revision and payment path stopped; preserve evidence and do not improvise destructive downgrade or row edits.

## Process commands

Private timestamp task service:

```bash
uvicorn app.task_main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers 1 --limit-concurrency 100 --timeout-keep-alive 5 --no-access-log
```

This service must require Cloud Run IAM. Its OIDC audience is the exact Cloud Run service origin, and only the dedicated Cloud Tasks caller service account gets `roles/run.invoker`. `allUsers` and `allAuthenticatedUsers` must not be invokers. The API and task-runtime identities enqueue successors; there is no routine timestamp worker loop or scheduler.

Notification worker:

```bash
export NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker
python -m app.notifications.cli --once
```

The notification worker omits `--once` when continuous. Optional instance identities are opaque and must not include hostnames, emails, credentials, or customer data.

Operator commands:

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.replay_job JOB_UUID --confirm REPLAY:JOB_UUID
python -m scripts.upgrade_order ORDER_UUID --confirm UPGRADE:ORDER_UUID
python -m scripts.reverify_order ORDER_UUID --request-id CHANGE_ID --confirm REVERIFY:ORDER_UUID:CHANGE_ID
python -m scripts.reconcile_tasks --limit 100
```

Inspect state, immutable binding, attempts, lease, proof versions, observations, and safe error first. Replay only an ordinary retry with no active lease. Upgrade only `calendar_pending`. Reverify only `bitcoin_verified` or `delivered`. Manual review is a refusal state: current operator code validates limited invariants and then refuses terminal recovery; never edit the state directly or replay around that guard.

`reconcile_tasks` without recovery options selects only `pending` dispatch intents and retries their deterministic Cloud Tasks create calls. Run it after a confirmed create outage or deployment interruption; do not schedule it routinely. A Cloud Tasks `AlreadyExists` result is idempotent and the intent is marked dispatched.

## Private Cloud Tasks checks

Before rollout, after IAM changes, and during any dispatch incident, verify the deployed topology using values obtained from the environment rather than assumed URLs:

```bash
TASK_SERVICE_URL="$(gcloud run services describe "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
gcloud run services get-iam-policy "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --flatten='bindings[].members' --filter='bindings.role=roles/run.invoker' \
  --format='table(bindings.members)'
gcloud tasks queues describe "$TASK_QUEUE" \
  --project="$PROJECT_ID" --location="$REGION"
gcloud tasks queues get-iam-policy "$TASK_QUEUE" \
  --project="$PROJECT_ID" --location="$REGION"
gcloud iam service-accounts get-iam-policy "$TASK_CALLER_SA" \
  --project="$PROJECT_ID"
UNAUTH_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$TASK_SERVICE_URL/health/live")"
case "$UNAUTH_STATUS" in 401|403) ;; *) exit 1 ;; esac
```

The Cloud Run IAM listing must contain the approved dedicated caller and no `allUsers`/`allAuthenticatedUsers`. Queue IAM must grant only approved runtime identities `roles/cloudtasks.enqueuer`; caller-service-account IAM must grant those identities `roles/iam.serviceAccountUser`. Confirm application configuration has `CLOUD_TASKS_WORKER_URL=$TASK_SERVICE_URL/internal/tasks/run` and `CLOUD_TASKS_AUDIENCE=$TASK_SERVICE_URL`. Do not print the database URL or other secrets while inspecting configuration.

For an owner-approved OIDC canary, enqueue a one-off `GET` to private `/health/live` with the dedicated caller and exact audience. It does not touch job data:

```bash
CANARY_TASK="iam-canary-$(date -u +%Y%m%d%H%M%S)"
gcloud tasks create-http-task "$CANARY_TASK" \
  --project="$PROJECT_ID" --location="$REGION" --queue="$TASK_QUEUE" \
  --url="$TASK_SERVICE_URL/health/live" --method=GET \
  --oidc-service-account-email="$TASK_CALLER_SA" \
  --oidc-token-audience="$TASK_SERVICE_URL"
```

Require one `200` task-service request, no retry, and no unauthenticated success. A `401`/`403` from the OIDC canary means IAM, caller identity, or audience is wrong. A `5xx` from `/internal/tasks/run` is retryable and must be diagnosed from safe error codes and opaque task/job IDs.

## Dispatch recovery

Normal job creation and every retry atomically persist a `pending` exact-generation dispatch intent. Cloud creation changes it to `dispatched`; task execution locks the job and rejects stale generations. This makes a create outage recoverable, but Cloud Tasks acknowledging create and later losing or exhausting the task requires explicit operator recovery.

Before stale recovery:

1. Confirm the current-generation intent is `dispatched`, the job is overdue/runnable, no lease is active, and the queue no longer contains or retries the task.
2. Diagnose task-service IAM, OIDC audience, queue pause/rate/retry state, and `5xx`; fix the cause first.
3. Wait beyond both the queue's retry duration and the stale grace. The recommended queue retry duration is one hour; code enforces a stale grace from two hours through 30 days.
4. Record the bounded job set and run one operator. Start with a small limit.

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.reconcile_tasks \
  --recover-stale-dispatched --stale-grace-seconds 7200 --limit 25
```

The recovery transaction locks each candidate, rechecks current generation/state/due time/attempt and lease bounds, marks the old dispatch `superseded_stale_dispatch`, increments `durable_jobs.generation`, and creates a deterministic pending intent scheduled now. It then dispatches the new name. An old delayed/retried task carries the prior generation and returns without claiming the new generation. If cloud creation fails, the new intent remains pending and the ordinary pending-only command can retry it.

Never use stale recovery for complete, manual-review, dead-letter, future-scheduled, attempt-exhausted, actively leased, or younger-than-grace records. Do not lower the grace to force progress, run concurrent broad recoveries, delete Cloud Tasks/dispatch rows, or edit generations directly. Reconcile output contains safe counts only; verify the expected old/new generation records and queue task before increasing the limit.

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
4. Decide and record whether the Cloud Tasks queue/private task service, already-paid timestamp jobs, confirmation monitoring, and notification delivery continue.
5. Reconcile every open, processing, paid, queued, leased, accepted, delivered, refund, and dispute record.
6. Do not assume existing Stripe sessions were canceled; expire/reconcile them through an authorized environment-specific procedure.

Calendar commitments already accepted cannot be canceled. Pausing the queue/task service only prevents new task execution; queued and durable work remains and must be reconciled before resumption.

Checkout idempotency uses a 5-300 second processing/grace lease (default 60). A concurrent identical retry may receive HTTP `425` without a token. Wait and retry the identical body and idempotency key; never change keys to bypass the lease.

## Token and secret rotation

Customer tokens travel only in `Authorization`. Use authenticated `POST /v1/orders/rotate-token`; the old token is atomically revoked and the replacement is returned once in the body. Support must never request a token by email/chat.

For pepper rotation, add a new strong `TOKEN_PEPPERS__N`, retain old versions still needed, update `ACTIVE_TOKEN_PEPPER_VERSION`, deploy all consumers consistently, rotate/expire dependent customer tokens, then remove an old pepper only when no valid token needs it.

Rotate Stripe keys/webhook secrets, Resend key/webhook secret, Neon credentials, Bitcoin RPC credentials, deployment access, and GitHub App access one class at a time under an approved plan. Pause the corresponding path, validate with synthetic evidence, revoke the old credential, and reconcile. Never place service secrets in `VITE_*`.

## Incident response

Critical examples include secret/PII exposure, unauthorized fulfillment, wrong-digest proof, false confirmation, unsafe reorganization handling, database compromise, or destructive loss. High examples include unqueued paid orders, webhook bypass, repeat corruption, token disclosure, missing backup, or initial delivery without matching evidence.

1. Declare an incident and assign an owner.
2. Pause checkout and the affected Cloud Tasks queue/task service or notification worker; revoke affected access.
3. Preserve append-only payment, proof, observation, job, consent, notification, refund, and dispute evidence.
4. Scope by opaque IDs without exporting unnecessary PII.
5. Restore only into isolation; reverify immutable bindings and proof/notification state before returning service.
6. Communicate only verified facts. Pending remains pending; `delivered` is email-delivery state, not stronger Bitcoin confirmation.
7. Obtain account-owner approval for customer/provider/legal communication and any changed launch gate.

## Monitoring and rollback

Monitor API health/error/latency, Neon readiness/connections/storage/backups, migration revision, checkout `425` age, Stripe webhook failures/reconciliation, Cloud Tasks queue depth/oldest task/retry exhaustion/rate throttling, pending and stale-dispatched intent age, private task-service `401`/`403`/`5xx`/latency, timestamp job leases/retries/manual review, six-hour upgrade gaps, 15-minute confirmation gaps, paid-to-stamped age, Bitcoin Core sync/RPC errors, confirmation decreases/conflicts, notification leases/retries/dead letters, Resend acceptance/delivery/bounce/complaint, DNS/TLS, restore drills, costs, refunds, and disputes. Alert before the two-hour stale threshold; stale recovery is a controlled incident action, not automatic remediation.

API `/health/live` proves only API process response. API `/health/ready` proves a configured database query. Private task-service `/health/live` proves only authenticated reachability when invoked through Cloud Tasks. None proves durable execution, successor dispatch, or external providers.

Rollback only to an immutable image compatible with `20260831_0003`. Set `CHECKOUT_ENABLED=false`, pause the Cloud Tasks queue, stop operator mutations, preserve queued tasks and all durable dispatch/job/payment/proof/observation/notification evidence, deploy the compatible replacement, verify private IAM/OIDC and schema head, reconcile pending intents, then resume gradually. Pre-`20260831_0003` images are incompatible because they do not maintain generation fencing or dispatch intents. Do not routinely downgrade to `20260827_0002`: that drops `task_dispatches` and `durable_jobs.generation` and destroys recovery evidence. Use a separately reviewed isolated downgrade/transition plan only if explicitly authorized; otherwise prefer forward repair. Refund, dispute, rollback, restore, token revocation, or account closure cannot erase a public OpenTimestamps/Bitcoin commitment.

## Neon validation record

On 2026-08-27, two expiring, non-primary Neon branches were cloned from production at the same parent LSN. Production remained idle with zero reported writes. The test branch reached the then-current `20260827_0002` and passed all 270 backend tests, including online upgrade/downgrade, trigger, reconciliation, and concurrency coverage. A separate clean branch started with zero application tables, reached that historical head with 16 tables and eight application triggers, and retained zero business/evidence rows. Both validation computes were suspended and the branches were configured to expire on 2026-08-28.

That historical exercise does not validate `20260831_0003`, Cloud Tasks IAM/OIDC, event dispatch/recovery, customer-data recovery, proof checksum comparison, or customer-proof reverification. Validate the new head first on a current isolated restore, continue backup monitoring, and repeat the restore drill when representative data exists.

## Remaining operational gates

Migration `20260831_0003` isolated-restore evidence, Cloud Tasks queue and private IAM/OIDC canaries, Neon backup monitoring and a future customer-data restore drill, Resend domain/DNS/webhook, private deployment controls, calendar pilot approval, private Bitcoin Core checks and reorganization policy, fresh Stripe-test canaries, Stripe tax/live webhook, production DNS/TLS, owner live canaries, policies, monitoring, and explicit launch approval remain incomplete. Keep corresponding modes disabled until the owner authorizes and evidence records each gate.
