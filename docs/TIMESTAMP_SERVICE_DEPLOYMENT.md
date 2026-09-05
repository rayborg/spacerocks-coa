# Timestamp Service Deployment Preparation

## Authority and current state

This is a preparation guide, not deployment authorization. The MVP code supports gated live operation, but production is not publicly live and real customer payments are unavailable. A Stripe-test sandbox checkout/refund exercise was recorded working previously; it has not been freshly verified and must not be represented as current health.

Neon is the current PostgreSQL target. `timestamp-service/railway.toml` is optional deployment metadata only: it neither selects a database/provider nor proves that any Railway resource exists. Do not create or alter cloud resources, DNS, provider accounts, webhooks, secrets, payments, or calendar commitments without the account owner's explicit approval.

## Source and environment isolation

Deploy only a reviewed immutable source revision from a private, protected backend boundary. Require CI, review, MFA, secret scanning, least-privilege deployment access, and separate staging/production identities. Keep databases, domains, Stripe objects and secrets, token peppers, Resend credentials/webhooks, logs, backups, budgets, workers, and frontend API URLs isolated. Never copy production customer data to staging.

All provider modes default to `disabled`. Staging must use synthetic records until privacy and retention policy allows otherwise.

## Backend variables

Secrets belong in the approved secret manager, never Git, chat, screenshots, logs, command history, support tickets, or `VITE_*` values.

| Variable | Condition and source-enforced rule |
| --- | --- |
| `APP_ENV` | `local`, `test`, `staging`, or `production`; fixture modes require `test` |
| `PAYMENT_MODE` | Default `disabled`; `stripe_test` requires test/staging plus its explicit gate; `stripe_live` requires production plus its explicit gate |
| `CHECKOUT_ENABLED` | Independent default `false` gate for creating new checkout sessions; enable only after Stripe and fulfillment are ready |
| `CALENDAR_MODE` | Default `disabled`; `public` requires staging/production and a valid allowlist |
| `BITCOIN_VERIFIER` | Default `disabled`; `bitcoin_core` requires staging/production and complete RPC configuration |
| `RESEND_SENDER_MODE` | Default `disabled`; `resend` requires database, API key, and sender |
| `RESEND_WEBHOOK_MODE` | Default `disabled`; `resend` requires database and valid signing secret |
| `DATABASE_URL` | Secret PostgreSQL URL; Neon is the current target and remote environments require PostgreSQL |
| `ALLOWED_ORIGINS` | JSON list of exact origins; staging/production entries must be HTTPS; no wildcard/credentials/path/query/fragment |
| `TOKEN_PEPPERS__N` | Secret, at least 32 bytes for each positive retained version |
| `ACTIVE_TOKEN_PEPPER_VERSION` | Names a configured pepper; required when payments are enabled |
| `TOKEN_TTL_SECONDS` | 300 through 7,776,000; default 2,592,000 |
| `CHECKOUT_CREDENTIAL_GRACE_SECONDS` | 5 through 300; default 60 |
| `STRIPE_TEST_ENABLED` | Explicit `true` gate for `stripe_test`; must be false in live mode |
| `STRIPE_LIVE_ENABLED` | Explicit `true` gate for `stripe_live`; must be false in test mode |
| `STRIPE_SECRET_KEY` | Matching `sk_test_`/`rk_test_` or `sk_live_`/`rk_live_` credential |
| `STRIPE_WEBHOOK_SECRET` | `whsec_` secret for the exact environment endpoint |
| `STRIPE_PRICE_ID` | Server-controlled `price_` identifier |
| `CHECKOUT_AMOUNT_MINOR` | Positive server-controlled amount; default 500 |
| `CHECKOUT_CURRENCY` | Lowercase three-letter code; default `usd` |
| `PRODUCT_VERSION` | Reviewed 1-64 character version; production cannot begin with `phase0` |
| `EXPECTED_TERMS_VERSION` | Safe 1-32 character version; production cannot begin with `phase0` |
| `EXPECTED_PRIVACY_VERSION` | Safe 1-32 character version; production cannot begin with `phase0` |
| `CHECKOUT_SUCCESS_URL` | HTTPS and origin present in `ALLOWED_ORIGINS` |
| `CHECKOUT_CANCEL_URL` | HTTPS and origin present in `ALLOWED_ORIGINS` |
| `STRIPE_AUTOMATIC_TAX_ENABLED` | Must remain `false`; `true` is hard-rejected |
| `STRIPE_SIGNATURE_TOLERANCE_SECONDS` | 60-600; default 300 |
| `STRIPE_API_TIMEOUT_SECONDS` | 1-30; default 10 |
| `CALENDAR_ALLOWLIST` | JSON list of 2-8 HTTPS URLs on at least two independent DNS hosts; only with public mode |
| `CALENDAR_TIMEOUT_SECONDS` | 0.1-30; default 5 |
| `BITCOIN_RPC_URL` | Private explicit HTTP(S) Bitcoin Core JSON-RPC URL without embedded credentials |
| `BITCOIN_RPC_USERNAME` | Secret RPC username |
| `BITCOIN_RPC_PASSWORD` | Secret RPC password |
| `BITCOIN_RPC_TIMEOUT_SECONDS` | 0.1-30; default 5 |
| `RESEND_API_KEY` | Secret `re_` key, sender mode only |
| `RESEND_SENDER` | Reviewed sender address, sender mode only |
| `RESEND_API_TIMEOUT_SECONDS` | 0.1-30; default 10 |
| `RESEND_WEBHOOK_SECRET` | Secret `whsec_` Svix-compatible signing secret, webhook mode only |
| `RESEND_WEBHOOK_TOLERANCE_SECONDS` | 1-900; default 300 |
| `TRUSTED_PROXY_IPS` | JSON list of exact verified immediate proxy IPs; default empty |
| `CHECKOUT_RATE_LIMIT` | Positive; default 10 |
| `WEBHOOK_RATE_LIMIT` | Positive; default 120 |
| `RESEND_WEBHOOK_RATE_LIMIT` | Positive; default 120 |
| `STATUS_RATE_LIMIT` | Positive; default 60 |
| `PROOF_RATE_LIMIT` | Positive; default 20 |
| `ROTATION_RATE_LIMIT` | Positive; default 5 |
| `TASK_DISPATCH_MODE` | `cloud_tasks` is mandatory for staging/production paid modes; `disabled` is fail-closed |
| `CLOUD_TASKS_PROJECT` | Project containing the dedicated queue |
| `CLOUD_TASKS_LOCATION` | Queue region; colocate with the private task service |
| `CLOUD_TASKS_QUEUE` | Dedicated queue name, also checked against `X-CloudTasks-QueueName` |
| `CLOUD_TASKS_WORKER_URL` | Exact private HTTPS URL ending in `/internal/tasks/run` |
| `CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL` | Dedicated OIDC caller identity; grant only Cloud Run invoker on the task service |
| `CLOUD_TASKS_AUDIENCE` | Exact task-service origin returned by Cloud Run, with no path |

Exact process settings:

| Process | Setting or command |
| --- | --- |
| Private Cloud Tasks service | `uvicorn app.task_main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --limit-concurrency 100 --timeout-keep-alive 5 --no-access-log` |
| Notification worker | `NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker` |
| Notification worker identity | Optional opaque `NOTIFICATION_WORKER_ID` |
| Operator task | `TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands` |

Do not set mode-specific credentials while that mode is disabled; settings reject stray Stripe, calendar, Bitcoin RPC, and Resend secrets/configuration.

## Frontend variables

Frontend configuration is public:

| Variable | Rule |
| --- | --- |
| `VITE_TIMESTAMP_API_URL` | Reviewed HTTPS API base; absent means no paid UI/requests |
| `VITE_TIMESTAMP_SERVICE_MODE` | `sandbox` or `production`; defaults to `sandbox` |
| `VITE_TIMESTAMP_POLICY_VERSION` | Required in production and cannot begin with `phase0` |
| `VITE_TIMESTAMP_TERMS_URL` | Required HTTPS policy URL in production |
| `VITE_TIMESTAMP_PRIVACY_URL` | Required HTTPS policy URL in production |
| `VITE_TIMESTAMP_REFUND_URL` | Required HTTPS policy URL in production |
| `VITE_TIMESTAMP_SUPPORT_EMAIL` | Required valid support email in production |

Policy URLs reject credentials, query, fragment, and unsafe paths. Never put tokens, emails, keys, database/RPC URLs, or provider secrets in frontend variables.

## Process topology

Run one reviewed image with separate process identities:

- API: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --limit-concurrency 100 --timeout-keep-alive 5 --no-access-log`
- private task service: `uvicorn app.task_main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --limit-concurrency 100 --timeout-keep-alive 5 --no-access-log`
- notification worker: `python -m app.notifications.cli`
- migration: serialized one-off `alembic upgrade head`
- operator scripts: controlled one-off tasks only
- Neon PostgreSQL: private connectivity, least privilege, monitored backups, and tested isolated restore
- Bitcoin Core: private authenticated RPC reachable only by approved backend processes

The image runs as UID/GID `10001:10001`. Prefer read-only root filesystems, a bounded `noexec,nosuid` `/tmp`, no Docker socket, no writable source mount, and dropped capabilities. Record compensating controls where the platform cannot enforce these restrictions.

The task service runs `app.task_main:app` on a separate Cloud Run service with authentication required and reviewed ingress. Never grant `allUsers` or `allAuthenticatedUsers` `roles/run.invoker`. The dedicated OIDC caller service account gets `roles/run.invoker` on this service only. API and task-runtime identities need `roles/cloudtasks.enqueuer` on the dedicated queue and permission to act as the OIDC caller; do not reuse a human or deployment identity. The Cloud Tasks service agent must retain its Google-managed service-agent role so it can mint OIDC tokens.

Configure a bounded dedicated queue. These values make the enforced two-hour stale-recovery floor longer than the one-hour queue retry window; change them only with a reviewed matching recovery policy:

```bash
gcloud tasks queues update "$TASK_QUEUE" \
  --project="$PROJECT_ID" --location="$REGION" \
  --max-dispatches-per-second=10 --max-concurrent-dispatches=10 \
  --max-attempts=20 --min-backoff=10s --max-backoff=300s \
  --max-doublings=5 --max-retry-duration=3600s
```

Deploy the task service with `--no-allow-unauthenticated`, then bind only the dedicated caller. Derive the URL from Cloud Run rather than typing it:

```bash
gcloud run deploy "$TASK_SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --image="$IMAGE_DIGEST" --service-account="$TASK_RUNTIME_SA" \
  --port=8000 --command=uvicorn \
  --args=app.task_main:app,--host,0.0.0.0,--port,8000,--workers,1,--limit-concurrency,100,--timeout-keep-alive,5,--no-access-log \
  --no-allow-unauthenticated
gcloud run services add-iam-policy-binding "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member="serviceAccount:$TASK_CALLER_SA" --role="roles/run.invoker"
for ENQUEUER_SA in "$API_RUNTIME_SA" "$TASK_RUNTIME_SA"; do
  gcloud tasks queues add-iam-policy-binding "$TASK_QUEUE" \
    --project="$PROJECT_ID" --location="$REGION" \
    --member="serviceAccount:$ENQUEUER_SA" --role="roles/cloudtasks.enqueuer"
  gcloud iam service-accounts add-iam-policy-binding "$TASK_CALLER_SA" \
    --project="$PROJECT_ID" --member="serviceAccount:$ENQUEUER_SA" \
    --role="roles/iam.serviceAccountUser"
done
TASK_SERVICE_URL="$(gcloud run services describe "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
export CLOUD_TASKS_WORKER_URL="$TASK_SERVICE_URL/internal/tasks/run"
export CLOUD_TASKS_AUDIENCE="$TASK_SERVICE_URL"
```

Before enabling paid traffic, require all of these checks. The first IAM query must produce no members; the second must show only approved invokers. An unauthenticated request must be rejected by Cloud Run with `401` or `403`, before application request validation:

```bash
gcloud run services get-iam-policy "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/run.invoker AND (bindings.members=allUsers OR bindings.members=allAuthenticatedUsers)' \
  --format='value(bindings.members)'
gcloud run services get-iam-policy "$TASK_SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --flatten='bindings[].members' --filter='bindings.role=roles/run.invoker' \
  --format='table(bindings.members)'
gcloud tasks queues get-iam-policy "$TASK_QUEUE" \
  --project="$PROJECT_ID" --location="$REGION"
gcloud iam service-accounts get-iam-policy "$TASK_CALLER_SA" \
  --project="$PROJECT_ID"
UNAUTH_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$TASK_SERVICE_URL/health/live")"
case "$UNAUTH_STATUS" in 401|403) ;; *) exit 1 ;; esac
```

Use a one-off GET task against private `/health/live` to prove queue-to-service OIDC without mutating job data. Verify one `200` request in Cloud Run logs and that the task completes without retry; then record the task name and non-sensitive log reference:

```bash
CANARY_TASK="iam-canary-$(date -u +%Y%m%d%H%M%S)"
gcloud tasks create-http-task "$CANARY_TASK" \
  --project="$PROJECT_ID" --location="$REGION" --queue="$TASK_QUEUE" \
  --url="$TASK_SERVICE_URL/health/live" --method=GET \
  --oidc-service-account-email="$TASK_CALLER_SA" \
  --oidc-token-audience="$TASK_SERVICE_URL"
```

Do not deploy a routine timestamp polling process or minute scheduler. Database transactions create exact-generation dispatch intents, Cloud Tasks invokes the private task service, and each task dispatches durable successors. `scripts.reconcile_tasks` is an operator-only repair command, not a scheduler target.

## Required deployment sequence

Every provider or network-changing step requires the applicable owner approval. Keep checkout disabled until the final go/no-go.

1. Record the reviewed source/image digest, environment, non-sensitive change ID, rollback compatibility, and owner-approved scope.
2. Verify private deployment boundaries, MFA/access, secret-manager references, budgets, logging redaction, edge controls, and staging/production isolation.
3. Create a current Neon backup, restore it into a clean isolated Neon target, and ensure API, private task service/queue, notification worker, webhooks, and provider egress are disabled there.
4. Inspect migration `20260831_0003_event_tasks.py` and its dependency on `20260827_0002`; run one serialized `alembic upgrade head`; verify `alembic current` is `20260831_0003`.
5. On the isolated restore, verify row counts, immutable order/digest bindings, all proof versions/checksums, bundles, Bitcoin observations, outbox/attempt records, and Resend webhook events. Reverify proofs only under the approved offline/private policy.
6. Configure production API settings with payment/calendar/Bitcoin/Resend modes still disabled. Deploy the API and verify `/health/live`, `/health/ready`, CORS, security headers, body limits, rate limits, and synthetic authenticated status/token rotation.
7. Create/update the dedicated queue, deploy private `app.task_main:app`, verify IAM has no public invoker, run the unauthenticated rejection and OIDC queue canaries above, and confirm exact audience, queue header, rate, concurrency, and retry settings. Do not deploy a polling scheduler.
8. Deploy the notification worker using `app.notifications.worker:create_notification_worker` only after the approved Resend sender key/domain exists; initially keep queue generation/provider sending paused as the runbook requires.
9. Configure private Bitcoin Core RPC, network ACLs, least-privilege credentials, mainnet/sync checks, timeout behavior, and monitoring. Never expose RPC publicly.
10. After independent review and explicit calendar-pilot authorization, configure `CALENDAR_MODE=public` and at least two allowlisted hosts. Run only synthetic random-digest pilots; preserve the irreversible proof and at-least-once crash semantics.
11. Configure Resend sending-domain DNS (SPF, DKIM, Return-Path, DMARC), `RESEND_SENDER_MODE=resend`, and signed webhook endpoint `POST /v1/webhooks/resend`. Verify delivered, bounced, failed, complained, duplicate, stale, malformed, and delivery-before-acceptance cases.
12. Register environment-specific Stripe webhook `POST /v1/webhooks/stripe`, preserve the raw body, and configure a server-controlled Price. Resolve Stripe Tax/accounting policy; the application currently hard-rejects automatic tax.
13. Freshly re-run Stripe-test checkout, decline/3DS where applicable, expiration, duplicate/out-of-order event, refund, dispute, and reconciliation canaries. Record that the prior recovered checkout/refund canaries were historical only.
14. Configure production API/frontend DNS and TLS, exact `ALLOWED_ORIGINS`, Stripe return URLs, policy URLs, support email, proxy trust, and edge controls. Verify certificate renewal and HTTP-to-HTTPS behavior.
15. With all policies and controls approved, prepare `APP_ENV=production`, `PAYMENT_MODE=stripe_live`, `STRIPE_LIVE_ENABLED=true`, and `CHECKOUT_ENABLED=false`; this permits webhook and fulfillment readiness without creating new checkout sessions.
16. While public access remains blocked, temporarily set `CHECKOUT_ENABLED=true`, run owner-controlled low-value live checkout/refund and fulfillment canaries, then restore `CHECKOUT_ENABLED=false`. Monitor API, Cloud Tasks queue/private task service, notification worker, Neon, calendars, Bitcoin Core, Stripe, and Resend, and reconcile every canary.
17. Obtain explicit owner go/no-go, set `CHECKOUT_ENABLED=true`, and open only invite-only access at first; set it back to `false` after any incomplete/failed gate.

## Webhooks and delivery semantics

Stripe endpoint: `POST /v1/webhooks/stripe`. A `2xx` means required payment state was durably persisted, not that timestamping completed. Browser success/cancel URLs never authorize fulfillment.

Resend endpoint: `POST /v1/webhooks/resend`. Preserve `svix-id`, `svix-timestamp`, `svix-signature`, and the raw body. Resend API acceptance records `accepted`; only a signature-verified matching `email.delivered` event records delivery. The initial message is bound to an observation at `>=1` confirmation and can move `bitcoin_verified` to `delivered`. The final message is enqueued at `>=6` and does not create another fulfillment state.

## Health and monitoring

`/health/live` proves process response. `/health/ready` checks the configured database. Neither proves Stripe, calendar, Bitcoin Core, Resend, workers, backup, restore, DNS, or TLS health.

Monitor API latency/errors, readiness, checkout `425` lease age, webhook rejects/replays, Cloud Tasks queue depth/oldest age/retry and dead-letter exhaustion, pending and stale-dispatched intent age, private task-service `401`/`403`/`5xx` and latency, paid-to-stamped age, six-hour upgrade successor gaps, 15-minute confirmation successor gaps, reorganization/manual-review events, proof mismatches, notification queue/lease/retry/dead-letter state, Resend delivery/bounce/complaint events, Neon connections/storage/backups, isolated restore drills, Bitcoin sync/RPC availability, DNS/TLS expiry, costs, refunds, and disputes.

## Rollback and prohibition

Rollback only to a `20260831_0003`-compatible immutable image. Set `CHECKOUT_ENABLED=false`, pause the Cloud Tasks queue, stop new operator mutations, preserve dispatch/job/payment/proof/observation/notification evidence, and prefer a forward fix. A pre-event image is not schema-compatible because it cannot maintain job generations or dispatch intents. Do not run `alembic downgrade 20260827_0002` as routine rollback: it drops `task_dispatches` and `durable_jobs.generation`, destroys recovery/fencing evidence, and requires a separately reviewed transition away from queued tasks. Resume the queue only after the replacement task service, IAM, schema head, and pending-intent reconciliation pass. Public calendar commitments remain irreversible after refund, rollback, restore, or deletion.

This guide does not authorize live mode, provider access, DNS changes, database migration, calendar submission, customer communication, or real payments. Explicit owner approval remains mandatory at each external gate and at final launch.
