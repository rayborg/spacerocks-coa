# Paid Timestamp MVP Session

Last verified: 2026-08-27T11:48:50Z

## Objective

Finish and publicly launch the optional paid managed Bitcoin timestamp service for Spacerocks COA Studio. The free browser-only COA workflow must remain independent and usable without payment, the backend, or Bitcoin.

## Repository

- Repository: `https://github.com/rayborg/spacerocks-coa`
- Branch: `feature/on-chain-anchoring`
- Latest implementation commit before this tracker update: `76f62cf`
- Paid MVP implementation commit: `562f986`
- Backend: `timestamp-service/`
- Public frontend and policies are in the same repository.
- The worktree was clean before this file was created.

## Owner Decisions

- Initial Bitcoin notice: at least 1 canonical confirmation.
- Final Bitcoin notice: at least 6 canonical confirmations.
- Verification source: owner-controlled Bitcoin Core RPC.
- Stripe Tax: launch without Stripe Tax; `automatic_tax` remains disabled. There is no Stripe Tax registration.
- Support address: `borgesraymond@gmail.com`.
- Email provider: Resend, using its free tier initially.
- Production infrastructure, deployment, provider setup, controlled canaries, and publishing were explicitly authorized.
- Public checkout must remain disabled until provider credentials, Bitcoin sync, email delivery, and live canaries pass.

## Code Complete

- Strict Stripe test/live isolation with `StripeClient`, Checkout Sessions, dynamic payment methods, pinned API version, webhook authority, refunds, and disputes.
- Hardened OpenTimestamps calendar transport with public-IP vetting, pinned connection IP, original hostname TLS/SNI validation, redirect refusal, and bounded responses.
- Bitcoin Core mainnet canonical-chain verification with exact 32-byte digest binding, stable confirmation counts, initial/final milestones, and reorganization handling.
- Durable Resend outbox, leases, attempts, signed webhooks, bounce/complaint handling, initial notice, and independent final notice.
- Append-only confirmation observations and provider evidence.
- Production frontend mode with versioned consent and policy links.
- Terms, Privacy, and Refund pages for policy version `paid-beta-v1`.
- Neon migration `20260827_0002`.

## Validation Evidence

- Backend unit/integration suite: 270/270 passed against an isolated real Neon PostgreSQL branch.
- Frontend unit tests: 41 passed.
- Strict mypy and Ruff: passed.
- TypeScript typecheck and production build: passed.
- GitHub Pages workflow: 36 browser tests passed before deployment.
- Focused preview change test: passed on Chrome, Firefox, and WebKit.
- Production OpenTimestamps calendar canary: accepted; generated proof size was 646 bytes.
- Production Cloud Run to private Bitcoin RPC canary: authenticated successfully before heavy initial-sync load.
- Independent code/integration and Neon isolation reviews: passed with no remaining critical/high code findings.

## Public Endpoints

- Frontend: `https://coa-sandbox.meteoriteresearch.org`
- Canonical GitHub Pages URL: `https://rayborg.github.io/spacerocks-coa/`
- API used by the production frontend: `https://timestamp-api-prod-907565713124.us-central1.run.app`
- API service URL reported by Cloud Run: `https://timestamp-api-prod-hnnpw4x2nq-uc.a.run.app`
- Terms: `https://coa-sandbox.meteoriteresearch.org/policies/terms.html`
- Privacy: `https://coa-sandbox.meteoriteresearch.org/policies/privacy.html`
- Refunds: `https://coa-sandbox.meteoriteresearch.org/policies/refunds.html`

The frontend and policies are public. The managed timestamp form appears after a COA package is generated. Customer checkout is still disabled.

## Production Infrastructure

### Google Cloud

- Project: `spacerocks-coa-production`
- Region: `us-central1`
- Artifact image: `us-central1-docker.pkg.dev/spacerocks-coa-production/timestamp-service/timestamp-service@sha256:8ad4e8825c7f8294e9afa0320d591ad6e7efae7ac013d6e4cce4735c00c86d7f`
- Public API service: `timestamp-api-prod`
- Latest ready API revision: `timestamp-api-prod-00002-rfq`
- API `/health/live`: HTTP 200
- API `/health/ready`: HTTP 200
- Timestamp worker job: `timestamp-worker-prod`
- Worker scheduler: `timestamp-worker-prod-minute`
- Worker cadence: `*/5 * * * *` UTC
- Scheduler state: enabled; latest attempt had no reported error.
- Calendar canary job: `timestamp-calendar-canary-prod`
- Bitcoin RPC canary job: `timestamp-bitcoin-canary-prod`
- Notification worker is not deployed because Resend is not configured.

### Neon

- Production `main` was empty before migration.
- Production is migrated to `20260827_0002`.
- Schema: 16 application tables and 8 application triggers.
- Runtime role has table DML only.
- Monitoring role has read-only table access.
- Five production database secrets contain enabled newline-free versions.
- Two isolated validation branches were configured to expire on 2026-08-28.
- Customer-data restore and proof reverification could not be tested because production contained no customer records.

### Bitcoin Core

- VM: `bitcoin-core-prod`
- Zone: `us-central1-a`
- Private RPC IP: `10.128.0.10:8332`
- Bitcoin Core: 31.1 mainnet, pruned, wallet disabled.
- Data disk: `bitcoin-core-data-prod`, 100 GB balanced persistent disk.
- Temporary sync machine: `e2-standard-4` with a 4 GB database cache.
- Last verified sync: block 438,664 of header 964,293.
- Verification progress: 0.12153518 (about 12.15%).
- Initial block download: true.
- Pruned data on disk: about 20.5 GB.
- Reduce the VM to `e2-medium` and restore `dbcache=1024` after initial sync completes.
- RPC is not public. Broad default GCP SSH/RDP/ICMP/internal firewall rules are disabled. Access is limited to IAP SSH and VPC RPC.

## Stripe State

- Live Stripe account is authenticated through the CLI.
- Live products and Prices exist for the managed manifest timestamp and image add-on.
- Live webhook endpoints: 0.
- Stripe Tax registrations: 0.
- `timestamp-stripe-restricted-live-key-prod` enabled secret versions: 0.
- `timestamp-stripe-live-webhook-secret-prod` enabled secret versions: 0.
- The CLI management key lacks `webhook_write`.
- Stripe dashboard login username: `borgesraymond@gmail.com`.
- Checkout remains disabled in the production API.

## Resend State

- This is the first Resend setup for the project.
- Resend onboarding was started with the owner Google account.
- `timestamp-resend-sending-key-prod` enabled secret versions: 0.
- `timestamp-resend-webhook-secret-prod` enabled secret versions: 0.
- No sending domain, DNS validation, sender, webhook, or email canary has been completed.
- The Free plan includes 3,000 emails/month and 100 emails/day, which is enough for the initial MVP.

## Immediate Blockers

1. Complete owner authentication/MFA in Stripe and Resend dashboards.
2. Create a restricted Stripe live application key and add a newline-free version to `timestamp-stripe-restricted-live-key-prod`.
3. Grant `webhook_write` to the Stripe CLI management key, create the live endpoint, and store its signing secret.
4. Create a Resend sending key, add a newline-free secret version, add/verify a sending subdomain in Bluehost DNS, create the webhook, and store its signing secret.
5. Deploy and schedule the notification worker, then verify signed `email.delivered`, bounce, complaint, retry, and two-stage message behavior.
6. Wait for Bitcoin Core `initialblockdownload=false`, rerun the application RPC canary, then reduce the VM size/cache.
7. Redeploy the API with Stripe live and Resend webhook modes enabled, while keeping checkout disabled.
8. Run owner-controlled low-value live payment, webhook, calendar, proof, one-confirmation email, six-confirmation email, and full-refund canaries.
9. Run an independent launch audit, then enable checkout and public customer traffic only if every canary passes.

## Secret Handling

- Never commit or print API keys, webhook secrets, database passwords, RPC passwords, status tokens, or connection strings.
- Stream provider credentials directly into GCP Secret Manager without trailing newlines.
- Use restricted Stripe keys, per-service GCP identities, and per-secret access bindings.
- Do not use browser success redirects as payment authority; only verified Stripe webhooks may authorize fulfillment.

## Operational Notes

- The public site is usable for free COA generation now, but paid timestamp purchase is not yet available.
- Direct PostgreSQL TLS from the local execution environment was unreliable; validation used Neon's supported WSS transport and a localhost-only bridge. Cloud Run uses Neon PostgreSQL secrets directly.
- Direct VPC Cloud Run Job cold starts can take roughly one to two minutes, so the worker schedule is five minutes to avoid overlapping no-op executions.
- Bitcoin RPC may time out during CPU-heavy initial sync; this is expected until the node reaches the tip.
- Automatic Stripe Tax must stay disabled unless an active registration is later confirmed.
