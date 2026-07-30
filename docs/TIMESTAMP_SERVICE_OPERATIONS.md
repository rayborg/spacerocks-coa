# Timestamp Service Operations

## Status and authority

This runbook describes Phase 0 sandbox procedures. It is not authorization to enable public calendars, email delivery, live payments, or production. Use synthetic data only. A pending proof, a browser return, or payment UI success must never be reported as Bitcoin-confirmed.

Only authorized operators may run migrations, mutate jobs, rotate service credentials, restore data, refund payments, or change checkout availability. Use least-privilege accounts and audited provider consoles. Enter secrets directly into the target secret manager or provider dashboard, never into chat, Git, command history, tickets, screenshots, logs, or frontend variables.

## Preflight

Before any operation:

1. Confirm the environment, deployment revision, database target, payment mode, calendar mode, and Bitcoin verifier mode.
2. Confirm Phase 0 remains sandbox-only and `stripe_live` is absent.
3. Record a non-sensitive incident/change reference and the opaque order or job IDs involved. Do not record bearer recovery tokens or secret values.
4. Pause conflicting deploys and ensure one operator owns each order mutation.
5. Verify a current backup exists for database-affecting work. For restore or migration work, use an isolated restore target first.

Public calendar operation is not available in the current runtime. Settings/composition expose only disabled or pytest-only fixture modes, and the dormant public transport refuses construction until pinned-public-IP TLS/SNI handling is implemented and reviewed. Do not attempt to enable it with undocumented variables or direct adapter construction.

## Migrations

Run migrations as a serialized release task before starting the new API and worker revision:

```bash
cd timestamp-service
alembic current
alembic upgrade head
alembic current
```

`DATABASE_URL` must come from the environment's secret store. Do not pass it on the command line. Review the migration and its downgrade behavior before execution, capture only non-sensitive output, and verify `/health/ready` plus schema revision afterward. Never let every API replica race to migrate. If a migration fails, keep the new revision stopped, preserve evidence, restore only according to the tested rollback plan, and do not improvise a destructive downgrade.

## Backup and restore gate

No provider-backed sandbox or live operation may begin until all of the following pass:

- scheduled Railway Postgres backups are enabled and monitored;
- encrypted off-provider backups include database rows and all proof bytes/versions;
- backup access uses a separate least-privilege identity and retention policy;
- restore has succeeded into a clean, isolated environment at the expected schema revision;
- restored row counts, immutable order bindings, job history, proof checksums, and exact target digests are verified;
- restored pending proofs remain pending and restored verified proofs pass independent reverification under an approved verifier/reorganization policy; and
- recovery point, recovery time, deletion, legal hold, and proof-retention requirements are approved.

Never test a restore over the source database. Prevent restored workers, webhooks, outbox consumers, and checkout endpoints from contacting providers. Rotate restored credentials or use disabled adapters before network access. Document the drill without customer data or secrets.

## Job replay, upgrade, and reverification

Set the operator factory only in the controlled operator process:

```bash
export TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands
python -m scripts.replay_job JOB_UUID --confirm REPLAY:JOB_UUID
python -m scripts.upgrade_order ORDER_UUID --confirm UPGRADE:ORDER_UUID
python -m scripts.reverify_order ORDER_UUID --request-id CHANGE_ID --confirm REVERIFY:ORDER_UUID:CHANGE_ID
```

Required checks:

- inspect immutable digest/order binding, current state, attempts, lease, existing proof versions, and safe error code first;
- replay only an ordinary job in retry state and confirm no healthy worker owns a live lease;
- upgrade only `calendar_pending`; retain every prior proof version;
- reverify only `bitcoin_verified` or a future `delivered` order, compare the proof target to the stored 32-byte digest, and apply the approved verification and Bitcoin-reorganization policy;
- do not mark an order confirmed because a command exited successfully; inspect the durable resulting state and evidence; and
- send target mismatch, corrupt/truncated proof, missing Bitcoin metadata, exhausted retries, or uncertain results to `manual_review`/incident handling rather than forcing success.

Manual review is a safe refusal state, not an operator retry queue. For a manual-review or dead-letter job, the current replay command may validate limited proof invariants but then raises `terminal_recovery_requires_ws1_state_transition`; no audited transition back to fulfillment exists. Keep the order unavailable and escalate for code/policy review rather than editing state or replaying around the refusal.

Ordinary Bitcoin confirmation pending follows a separate path from failures: each successful pending check completes and schedules a durable successor six hours later. It does not consume a short retry budget or dead-letter solely because confirmation is still pending, including beyond seven days. Outages, invalid data, and unexpected errors still use bounded retry/manual-review behavior. Maximum polling duration, proof/token/data retention, customer escalation, and eventual disposition must be approved before live operation.

The Phase 0 fixture adapters run only inside `pytest` and are not public evidence. Public-calendar replay and production reverification remain blocked until transport and policy gates pass review. Current successful reverification can reject changed immutable block metadata, but live use additionally requires a typed append-only record for every reverification request/result, verifier and policy versions, confirmation depth, reorganization observations, previous/current evidence, and operator reason. Generic state evidence is not sufficient live history.

## Calendar crash semantics

Calendar acceptance and the local Postgres proof append cannot share a transaction. If the response proof is already durably appended, replay validates and reuses it. If the calendar accepted the request but the worker crashed before append, no local artifact proves acceptance; recovery may at-least-once resubmit the same immutable 32-byte digest. Record this as an expected recovery ambiguity, preserve all returned proof versions, and never describe submission as exactly once or as a unique Bitcoin transaction. A repeated commitment of the same digest does not alter the digest binding.

## Durable proof and download rules

- A raw `.ots` proof must be 1-262,144 bytes. Parser, domain object, database constraint, and receipt contract use this same cap.
- Every proof version is append-only with target digest, checksum, byte length, state, and original calendar-submission time. Upgrades append a successor; they never overwrite historical bytes.
- The highest valid version is the current cryptographic proof artifact. Current state and matching bundle readiness separately determine whether it may be projected or downloaded; durable historical rows do not override order state.
- `stamping` projects `proof_available=false` and no calendar-submission or Bitcoin-verification timestamps, even if historical artifact rows exist.
- Pending download generation selects the latest metadata, builds outside the transaction, then rechecks token validity, immutable order binding, `calendar_pending`, and unchanged latest metadata before recording/returning the artifact.
- Verification may transition the order to `bitcoin_verified` before the separate bundle job finishes. During that interval `proof_available=false`; readiness becomes true only after a persisted bundle is bound to the current verified proof version and passes length/checksum validation. A future `delivered` state may use the same invariant.
- `manual_review` makes `proof_available` false and blocks download even when historical proof/verification/bundle rows remain. Do not delete those rows or serve around the refusal.
- Without an email sender, bundle and outbox creation does not prove delivery and does not transition the order beyond `bitcoin_verified`.

## Recovery token rotation

Customer bearer tokens belong only in the `Authorization` header. They must not appear in URLs, logs, analytics, email subjects, support records, or filenames.

For a customer token, use the authenticated `POST /v1/orders/rotate-token` flow. The previous token is revoked atomically. Return the replacement once through the authenticated response and advise private storage; support must never ask a customer to disclose it through chat or email.

For a hashing-pepper rotation:

1. Generate a new random secret in the environment secret manager and assign a new positive `TOKEN_PEPPERS__N` version.
2. Keep every still-needed old version configured and set `ACTIVE_TOKEN_PEPPER_VERSION` to the new version.
3. Deploy API, worker, and operator processes consistently and validate status/rotation with synthetic tokens.
4. Rotate active customer tokens or wait for old tokens to expire under the approved policy.
5. Remove an old pepper only when no valid token depends on it; premature removal revokes access.

Suspected token disclosure requires immediate token rotation, sanitized log review, scope assessment, and customer notification under the approved incident policy.

## Pause checkout

Pause new checkout at the API edge first, leaving health and authenticated status/proof recovery available. Then deploy `PAYMENT_MODE=disabled` and remove Stripe-specific variables because settings reject Stripe configuration in disabled mode. Preserve `DATABASE_URL`, token peppers, and status/proof access. Do not infer that already-created Stripe sessions are canceled; expire or reconcile them through the authorized Stripe sandbox procedure.

Decide separately whether already-paid durable jobs should continue or pause. Calendar submission is irreversible once accepted, so stopping workers does not retract existing commitments. Record the cutoff and reconcile every open, processing, paid, and queued order before resuming.

Checkout reservations use a committed provider-processing lease and post-completion credential grace interval, configured from 5 through 300 seconds (60 seconds by default). A concurrent identical retry can receive HTTP `425` with no status token. Preserve the same body and idempotency key, wait for the lease/grace interval, and retry; do not create a new key to bypass the lease. Monitor repeated `425` responses separately from provider failure.

## Incident response

Severity examples:

- Critical: secret/private-data exposure, unauthorized fulfillment, wrong-digest proof, false Bitcoin confirmation, database compromise, or destructive loss.
- High: paid orders not durably queued, webhook verification bypass, repeated proof corruption, backup failure beyond tolerance, or status-token leakage.
- Moderate: provider outage, pending proofs beyond the approved escalation age, exhausted error retries, readiness degradation, or email/outbox backlog in a future approved integration. Ordinary pending itself is not dead-letter exhaustion.

Response sequence:

1. Declare the incident, assign an incident owner, and preserve timestamps and sanitized evidence.
2. Pause checkout and affected workers; block compromised credentials and access paths.
3. Do not delete immutable order, payment, proof, job, consent, outbox/future-delivery, refund, or dispute evidence.
4. Determine affected orders by opaque ID and immutable digest binding without exporting unnecessary PII.
5. Rotate/revoke credentials, tokens, GitHub App access, and provider sessions according to scope.
6. Restore only from verified backups into isolation; reverify proof targets and states before service restoration.
7. Communicate only verified facts. Pending remains pending, and payment or calendar acceptance is not Bitcoin confirmation.
8. Obtain account-owner approval for customer/provider/legal notifications and document corrective actions and gate changes.

## Secret rotation

Rotate one credential class at a time with overlap only when the provider supports safe overlap:

- Stripe sandbox restricted key: create a least-privilege replacement, update staging, validate a synthetic checkout, revoke the old key, and reconcile events.
- Stripe sandbox webhook secret: support both endpoints during a controlled transition or stop checkout, update the endpoint secret, validate signed sandbox events, then remove the old endpoint.
- Railway/database credentials: pause mutations as needed, update every consumer atomically, verify readiness and worker claims, and revoke the old credential.
- email provider key, if later approved: pause outbox delivery, replace the sending-only key, test an approved synthetic recipient, revoke the old key, and inspect bounces.
- GitHub App: review installation scope and repository access; revoke and reinstall only with account-owner authorization.

Never put service secrets in `VITE_*` variables. Frontend configuration is public.

## Refund and dispute evidence

Refunds and disputes change the commercial state but cannot erase a calendar or Bitcoin commitment. Preserve:

- immutable order/digest/certificate binding and server-controlled amount, currency, product version, and mode;
- consent and policy versions/timestamp;
- canonical Checkout Session, PaymentIntent, signed webhook event references, and checkout processing/grace lease evidence;
- idempotent state transitions, job attempts, proof versions/checksums, verification and reverification history, outbox/future-delivery, and download audit evidence permitted by policy; and
- refund/dispute decisions and provider references without card data or bearer tokens.

Use the approved account-owner policy and Stripe dashboard/API procedure. Do not promise deletion of append-only timestamp evidence, and do not claim a refund withdrew a commitment.

## Health and monitoring

- `/health/live` proves only that the API process responds.
- `/health/ready` proves configured store availability and a database query; it does not test Stripe, calendars, Bitcoin verification, email, backups, or workers.
- Monitor API error/latency rates, readiness, checkout `425`/lease age, Postgres connections/storage, migration revision, worker heartbeat/lease age, queued/retry/exhausted error jobs, six-hour successor scheduling gaps, paid-to-stamped age, pending-proof age, target mismatches, webhook rejects, rate limiting, outbox backlog, backup success, restore drills, and budget/storage thresholds.
- Alert separately on refund/dispute events and invalid webhook spikes. Logs must use allowlisted paths and opaque IDs, never request bodies, authorization headers, emails, digests joined with PII, or secrets.

## Rollback

Application rollback is allowed only when the old revision is schema-compatible and still enforces the current security gates. Stop new checkout, stop or drain workers, deploy the reviewed prior image by immutable digest, verify migration compatibility, then check liveness, readiness, token authentication, and existing order states with synthetic records.

Database downgrade is not the default rollback. Preserve append-only events and proof versions. Never roll back by deleting proof or payment evidence. If compatibility cannot be guaranteed, remain paused and restore forward through a corrected release.

## Irreversible commitments

An OpenTimestamps calendar submission can be aggregated and later committed to Bitcoin. It cannot be recalled, deleted from Bitcoin, or undone by refund, dispute, database restore, token revocation, account closure, or application rollback. Avoid submitting PII: only the 32-byte digest target is used. A crash before local proof append may cause at-least-once resubmission of that same digest. Treat even synthetic digest submission as irreversible and require explicit authorization plus reviewed pinned-public-IP TLS/SNI transport before any public-calendar pilot.
