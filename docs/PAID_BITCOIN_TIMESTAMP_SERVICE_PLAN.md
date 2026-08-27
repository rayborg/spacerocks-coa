# Paid Bitcoin Timestamp Service Plan

**Status:** The current MVP scope is code-complete. Production is not publicly live, real customer payments are unavailable, and this document does not authorize launch.

**Last reviewed:** 2026-08-27

The optional service timestamps the customer-supplied SHA-256 of the exact final `manifest.json` bytes. The browser-generated, locally signed COA remains the foundational product and remains independently verifiable without this service.

## Recovered operational state

- A Stripe-test sandbox was previously deployed, and checkout/refund canaries were recorded as working.
- Those recovered canary results have not been freshly verified. They are historical records, not evidence of current provider, endpoint, secret, webhook, database, or deployment health.
- No production timestamp service is publicly live and no real customer payment path is available.
- Neon is the current database target. Migration and empty-production isolated-branch validation passed on 2026-08-27; customer-data restore/reverification remains a future gate when representative data exists.
- Resend/DNS, private deployment checks, Stripe tax and live webhook configuration, production DNS/TLS, and owner-run live canaries remain blocked.

No cloud state, URL, account, credential, DNS record, database, or provider configuration may be inferred from code or this recovered record.

## Decision summary

| Area | Current direction |
| --- | --- |
| Checkout | Stripe-hosted Checkout with server-controlled Price, amount, currency, and quantity |
| Timestamp | Exact 32-byte digest submitted to multiple allowlisted public OpenTimestamps calendars |
| Verification | Authenticated private Bitcoin Core JSON-RPC against canonical mainnet data |
| Database | Neon PostgreSQL target; empty-production migration drill passed, ongoing backup and future customer-data restore verification required |
| Workers | Separate durable timestamp and notification workers backed by PostgreSQL |
| Delivery | Authenticated status/proof plus Resend initial and final confirmation notices |
| Deployment | Provider-neutral reviewed image; `timestamp-service/railway.toml` is optional metadata only |
| Launch | Disabled until all external gates pass and the account owner explicitly approves |

## Implemented boundary

The source implements:

- an optional frontend with explicit `sandbox` and `production` modes;
- FastAPI checkout, Stripe webhook, status, proof, token rotation, Resend webhook, liveness, and readiness routes;
- gated `stripe_test` and `stripe_live` payment composition;
- durable orders, provider idempotency, events, jobs, append-only proof versions, Bitcoin observations, proof bundles, notification attempts, and Resend webhook evidence;
- `CALENDAR_MODE=public` through `MultiCalendarTimestamper` and `HardenedCalendarTransport`;
- `BITCOIN_VERIFIER=bitcoin_core` through `BitcoinCoreRpcTransport` and `BitcoinCoreRpcVerifier`;
- `RESEND_SENDER_MODE=resend` through `ResendEmailSender` and the durable notification worker;
- `RESEND_WEBHOOK_MODE=resend` through signed `POST /v1/webhooks/resend` processing;
- migration `20260827_0002`, which adds confirmation observations and durable Resend delivery evidence;
- deterministic fixture adapters restricted to `APP_ENV=test` in pytest; and
- tests that use mocks/fixtures rather than contacting providers.

Code presence satisfies no provider, deployment, policy, security-review, or owner-authorization gate. All provider modes default to `disabled`.

## Non-goals and claims

The service does not:

- receive a private signing key, passphrase, image, complete manifest, COA ZIP, physical address, provenance record, or card credential;
- independently establish that the digest belongs to a genuine or truthful COA;
- prove specimen authenticity, ownership, authorship, issuer identity, or provenance truth;
- put the certificate, manifest, photographs, or PII on Bitcoin;
- promise a unique Bitcoin transaction or an exact confirmation time; or
- treat a browser redirect, payment UI, calendar acceptance, pending proof, or email as Bitcoin verification.

Correct claim:

> The supplemental OpenTimestamps proof demonstrates that the submitted SHA-256 manifest digest was committed into Bitcoin by the referenced block. Verify it independently against the original manifest.

The proof establishes an existence-before bound. Public OpenTimestamps calendars are free; the service fee covers managed checkout, automation, monitoring, proof retention/upgrades, delivery, and support.

## Exact digest contract

1. The browser hashes the exact final UTF-8 bytes of `manifest.json` once with SHA-256.
2. Checkout sends a lowercase 64-character digest.
3. The API freezes the digest with the certificate/order binding before Checkout.
4. The worker decodes the hex to the original 32 bytes.
5. The worker constructs the detached OpenTimestamps target from those bytes without hashing the hex text or digest again.
6. Every pending/upgraded proof is parsed and checked against the exact stored digest.

Calendar submission and PostgreSQL proof append cannot be one transaction. If a calendar accepted the digest but the worker crashed before append, recovery may submit the same immutable digest again. This is at-least-once behavior, not exactly-once behavior or a unique-transaction guarantee.

## Runtime flow

1. The customer creates and signs the COA locally, then explicitly opts in.
2. The frontend sends certificate reference, exact manifest digest, delivery email, and versioned consent only.
3. The API creates an immutable order and hashed bearer recovery token.
4. Stripe-hosted Checkout collects card details. The browser return remains non-authoritative.
5. A valid Stripe webhook and canonical provider checks move payment to `paid` and enqueue timestamp work.
6. The timestamp worker submits the exact digest to at least one of the configured calendars and preserves the pending proof.
7. Six-hour durable successor jobs upgrade pending proofs until Bitcoin evidence is available. Ordinary pending does not consume a short error retry budget.
8. Bitcoin Core verifies mainnet synchronization, canonical block/header data, exact Merkle-root attestation, and at least one confirmation.
9. The service persists verification and an append-only confirmation observation, then sets `bitcoin_verified`.
10. A separate delivery job persists the proof bundle and enqueues the initial notice with evidence of `>=1` confirmation.
11. The notification worker sends through Resend. Provider acceptance alone does not mean delivery.
12. A signature-verified Resend `email.delivered` webhook for the initial notice moves `bitcoin_verified` to `delivered` after database evidence checks.
13. Fifteen-minute confirmation monitoring records monotonic observations. At `>=6`, it enqueues the final notice; final email delivery does not create another fulfillment state.
14. If confirmation disappears, decreases, or immutable block evidence conflicts, automation fails closed to `manual_review`. Historical proof/bundle/email rows remain evidence but do not authorize download or recovery.

`bitcoin_verified` can temporarily report `proof_available=false` before its matching bundle is persisted. `stamping` and `manual_review` suppress proof download. Manual review has no generic transition back into fulfillment.

## Configuration gates

Backend setting names are defined by `Settings`:

- modes: `APP_ENV`, `PAYMENT_MODE`, `CALENDAR_MODE`, `BITCOIN_VERIFIER`, `RESEND_SENDER_MODE`, `RESEND_WEBHOOK_MODE`;
- database/tokens: `DATABASE_URL`, `TOKEN_PEPPERS__N`, `ACTIVE_TOKEN_PEPPER_VERSION`, `TOKEN_TTL_SECONDS`;
- Stripe: `STRIPE_TEST_ENABLED`, `STRIPE_LIVE_ENABLED`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, `CHECKOUT_AMOUNT_MINOR`, `CHECKOUT_CURRENCY`, `PRODUCT_VERSION`, `EXPECTED_TERMS_VERSION`, `EXPECTED_PRIVACY_VERSION`, `CHECKOUT_SUCCESS_URL`, `CHECKOUT_CANCEL_URL`, `STRIPE_AUTOMATIC_TAX_ENABLED`, `STRIPE_SIGNATURE_TOLERANCE_SECONDS`, `STRIPE_API_TIMEOUT_SECONDS`, `CHECKOUT_CREDENTIAL_GRACE_SECONDS`;
- calendars: `CALENDAR_ALLOWLIST`, `CALENDAR_TIMEOUT_SECONDS`;
- Bitcoin Core: `BITCOIN_RPC_URL`, `BITCOIN_RPC_USERNAME`, `BITCOIN_RPC_PASSWORD`, `BITCOIN_RPC_TIMEOUT_SECONDS`;
- Resend: `RESEND_API_KEY`, `RESEND_SENDER`, `RESEND_API_TIMEOUT_SECONDS`, `RESEND_WEBHOOK_SECRET`, `RESEND_WEBHOOK_TOLERANCE_SECONDS`;
- network/limits: `ALLOWED_ORIGINS`, `TRUSTED_PROXY_IPS`, `CHECKOUT_RATE_LIMIT`, `WEBHOOK_RATE_LIMIT`, `RESEND_WEBHOOK_RATE_LIMIT`, `STATUS_RATE_LIMIT`, `PROOF_RATE_LIMIT`, `ROTATION_RATE_LIMIT`.

Exact process factories:

- `TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker`
- `NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker`
- `TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands`

Optional opaque process IDs are `TIMESTAMP_WORKER_ID` and `NOTIFICATION_WORKER_ID`.

`stripe_live` is implemented but fail-closed. It requires `APP_ENV=production`, `PAYMENT_MODE=stripe_live`, `STRIPE_LIVE_ENABLED=true`, `STRIPE_TEST_ENABLED=false`, matching live key/webhook/Price configuration, HTTPS allowlisted return origins, PostgreSQL, a strong active token pepper, and production product/terms/privacy versions that do not begin with `phase0`. `STRIPE_AUTOMATIC_TAX_ENABLED=true` is hard-rejected; the owner must resolve the tax configuration before launch.

Frontend public variables are:

- `VITE_TIMESTAMP_API_URL`
- `VITE_TIMESTAMP_SERVICE_MODE` (`sandbox` or `production`)
- `VITE_TIMESTAMP_POLICY_VERSION`
- `VITE_TIMESTAMP_TERMS_URL`
- `VITE_TIMESTAMP_PRIVACY_URL`
- `VITE_TIMESTAMP_REFUND_URL`
- `VITE_TIMESTAMP_SUPPORT_EMAIL`

Production frontend mode fails closed unless the policy version is non-Phase-0, policy URLs are HTTPS without credentials/query/fragment, and a valid support email is present. No secret or bearer token belongs in `VITE_*`.

## Security and data controls

- Bearer status/proof tokens travel only in `Authorization`; only hashes are stored.
- CORS is not authentication. Origins are exact and HTTPS in staging/production.
- Stripe and Resend webhooks verify raw-body signatures and bounded timestamp tolerances.
- Public calendar URLs are operator allowlisted. Requests reject unsafe DNS snapshots, pin a vetted public IP, preserve hostname TLS/SNI, reject redirects, disable proxy environment trust, and bound concurrency and response size.
- Bitcoin Core RPC must be private, authenticated, least privilege, and unavailable from the public Internet.
- Resend templates contain only the order reference and fixed text; status tokens and digests are not emailed.
- Logs must exclude request bodies, tokens, emails, provider signatures/secrets, database URLs, RPC credentials, and proof bytes.
- Proof versions, confirmation observations, provider events, and required delivery evidence are durable. Refunds, disputes, rollback, and restore cannot erase a public timestamp commitment.

## Current external gates

The following remain release-blocking and owner-controlled:

1. Review and approve the exact source revision, private deployment boundary, access controls, budgets, monitoring, and rollback plan.
2. Provision/validate Neon privately; apply through `20260827_0002`; complete an isolated restore, row/checksum review, and proof reverification drill with all provider egress disabled.
3. Approve the public-calendar allowlist and hardened transport review; authorize a synthetic random-digest pilot and record irreversible at-least-once behavior.
4. Provision a private synchronized Bitcoin Core mainnet RPC; approve confirmation/reorganization policy and exercise loss/decrease/conflict paths to `manual_review`.
5. Approve Resend, verify sending domain DNS (SPF, DKIM, Return-Path, DMARC), configure the signed webhook, and exercise delivered/bounced/failed/complained handling.
6. Publish and approve terms, privacy, refund/cancellation, delivery timing, support, retention/deletion, claims, calendar-delay, incident, dispute, and service-failure policies.
7. Complete Stripe account activation, live restricted key/Price/webhook setup, tax/accounting/legal decisions, instant-card scope, refund/dispute procedures, and separation from sandbox.
8. Configure production API/frontend DNS and TLS, exact CORS/return origins, proxy trust, edge limits, secret management, backups, alerts, and incident response.
9. Re-run current Stripe-test checkout/refund and failure canaries; the recovered canaries are not a substitute.
10. Run owner-controlled live canaries with explicit approval, low limits, monitored API/timestamp/notification workers, and reconciliation before invite-only access.

No gate may be inferred complete from tests, code, optional `railway.toml`, or recovered sandbox notes. The account owner must explicitly authorize provider changes, calendar pilot, live credentials, DNS, migration/restore work, live canaries, and public launch.

## Owner-only responsibilities

The account owner, not engineering documentation or an automated agent, must personally:

- accept provider terms and choose the Stripe account country, legal entity, business details, statement descriptor, payout bank, and settlement settings;
- complete identity/business/beneficial-owner verification and provide tax, bank, or identity records directly to the relevant provider;
- approve price, supported payment methods, refund/cancellation/dispute rules, support commitments, customer claims, retention/deletion, and incident remedies;
- obtain appropriate tax, accounting, privacy, and legal advice and decide registrations, collection, filing, and remittance duties;
- approve Resend as a processor, control the sending domain, authorize DNS changes, and approve sender/support identities;
- approve the calendar allowlist, irreversible synthetic pilot, Bitcoin Core trust/reorganization policy, and any managed infrastructure/provider;
- enter and rotate secrets directly in provider dashboards/secret managers and secure passwords, MFA methods, and recovery codes;
- approve deployment repository/provider access, Neon migration/restore work, production DNS/TLS, live canaries, invite-only access, and public launch; and
- monitor payments, payouts, refunds, disputes, provider verification requests, invoices, quotas, reputation, backups, incidents, and recurring costs.

Credentials, recovery codes, bank/tax details, and identity documents must never be supplied through chat, committed to the repository, or exposed to frontend code. Every owner action above remains blocked until the owner explicitly performs or authorizes it.

## Acceptance before public launch

- Wrong signatures, mode, amount, currency, Price, metadata, payment state, digest, proof, block evidence, and webhook replay all fail closed.
- Duplicate/concurrent/out-of-order provider events remain idempotent.
- Worker crashes recover without corrupting durable state; calendar acceptance-before-append behavior is documented and tested.
- Neon migration and isolated restore preserve immutable bindings, all proof versions, bundles, confirmation observations, notification attempts, and webhook evidence.
- Initial email requires `>=1`; `delivered` requires the matching Resend delivery webhook; final email requires `>=6`.
- Reorganization simulations move unsafe orders to `manual_review` and suppress downloads.
- Backups, alerts, reconciliation, refunds, disputes, token rotation, secret rotation, and rollback are exercised.
- Production URLs, policies, support, tax posture, price, retention, and owner approval are recorded without secrets.

Until these checks pass, keep payment, calendar, Bitcoin, sender, and Resend webhook modes disabled in production.
