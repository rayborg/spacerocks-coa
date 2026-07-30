# Paid Bitcoin Timestamp Service Plan

**Status:** Phase 0 implementation is present on `feature/on-chain-anchoring`; sandbox and live operation remain gated.

**Last reviewed:** 2026-07-30

This document preserves the complete audited plan and records the current implementation boundary for an optional, paid Bitcoin timestamp service. Phase 0 code exists on the feature branch, but the currently deployed website does not offer a live paid service.

The existing locally signed, offline-verifiable COA remains the foundational product. Payment processing and Bitcoin timestamping must remain optional supplemental services.

## Decision Summary

| Decision | Selected direction |
| --- | --- |
| Fulfillment | Fully automated |
| Checkout | Dynamic Stripe-hosted Checkout Session |
| Timestamp protocol | OpenTimestamps using Bitcoin |
| Backend | Separate Python/FastAPI service on Railway |
| Database | Railway Postgres |
| Background processing | Durable database-backed worker jobs |
| Customer delivery | Status page plus transactional email |
| Email provider | Resend proposed; final approval still required |
| Price | Decide after sandbox testing |
| Current implementation status | Phase 0 deterministic code and tests present; external sandbox and every live gate remain blocked |

### Current implementation boundary

Implemented on the feature branch:

- versioned request, response, receipt, and OpenAPI contracts with known-digest fixtures;
- an optional frontend that is absent unless `VITE_TIMESTAMP_API_URL` is configured, sends an allowlisted checkout body, keeps bearer recovery tokens out of URLs, and distinguishes pending from verified states;
- FastAPI checkout, signed-webhook, authenticated status/proof, token-rotation, liveness, and database-readiness routes;
- immutable order bindings, Postgres migration, idempotency and Stripe-event controls, rate limits, durable job claims, proof history, receipt bundling, and an outbox record;
- deterministic fixture payment, calendar, and Bitcoin-verification adapters restricted to `APP_ENV=test` inside `pytest`;
- a Stripe test-mode adapter and canonical payment checks, with tests that do not contact Stripe;
- durable jobs with leases, six-hour ordinary-pending successor polls, separate verified-bundle persistence, worker and operator factories, replay/upgrade/reverification commands, a non-root image, local Compose definition, and Railway preparation; and
- unit, contract, API, security, failure, worker, migration, frontend, and mocked browser tests.

Not implemented or not authorized for operation:

- live payment mode, which the Phase 0 settings reject;
- an approved Stripe sandbox account, Price, webhook endpoint, or end-to-end provider exercise;
- a reviewed public OpenTimestamps transport or production Bitcoin verification source. Calendar parsing and fan-out code exists, but settings/composition cannot select it and the transport intentionally refuses operation until pinned-public-IP TLS/SNI handling passes review;
- a transactional email sender or approved provider;
- a `delivered` transition: without a sender, verified bundle/outbox creation leaves the order at `bitcoin_verified`;
- typed append-only history for repeated live reverification and an approved verifier/Bitcoin-reorganization policy;
- approved price, terms, privacy, refund, dispute, support, tax, legal, retention, and deletion policies;
- monitored staging or production infrastructure, tested backup/restore, off-provider backup, alerting, or a live deployment; and
- completion of the sandbox acceptance gates in section 24.

Code presence does not satisfy an operational gate. Fixture confirmation is synthetic and pytest-only, a browser redirect is non-authoritative, calendar-pending is not Bitcoin-confirmed, and no live launch is authorized by this document.

## 1. Purpose

The future service will let a customer pay for managed timestamping of the exact SHA-256 digest of a signed COA manifest.

The managed service will:

- Create a Stripe-hosted Checkout Session.
- Confirm payment using a signed Stripe webhook.
- Submit the exact 32-byte manifest digest to multiple OpenTimestamps calendars.
- Preserve the initial pending `.ots` proof.
- Periodically upgrade the proof until it contains a Bitcoin attestation.
- Verify that the proof targets the exact submitted digest.
- Store the proof and its verification metadata durably.
- Notify the customer and provide a repeatable proof download.

## 2. Non-Goals

The service will not:

- Send or store a COA private signing key.
- Send or store specimen photographs.
- Send or store the complete COA ZIP.
- Put photographs, personal information, or complete certificate contents on Bitcoin.
- Replace the locally signed COA package.
- Prove that statements in a manifest are true.
- Prove ownership of a meteorite.
- Prove the identity of an issuer by itself.
- Promise a unique Bitcoin transaction for each certificate.
- Promise an exact Bitcoin confirmation time.
- Treat a successful browser redirect as proof of payment.

## 3. Audit Verdict

The architecture is viable with release-blocking safeguards.

The safe customer product is:

> A managed service that timestamps the customer-supplied COA manifest digest through OpenTimestamps and delivers a Bitcoin-attested proof.

The service must not claim that the backend independently validated the underlying COA unless a future validation protocol is explicitly implemented.

The service must make clear that public OpenTimestamps calendars are free. The customer charge covers the managed checkout, automation, monitoring, proof retention, upgrading, delivery, and support.

## 4. Foundational Principle

The original signed COA package remains independently verifiable without:

- Stripe
- Railway
- Resend
- OpenTimestamps calendars
- Bitcoin
- This website
- Any company continuing to exist

The paid timestamp proof is separate supplemental evidence. Loss of the paid service must not invalidate the original COA.

## 5. Correct Customer Claims

Acceptable wording:

> This supplemental OpenTimestamps proof demonstrates that the submitted SHA-256 manifest digest was committed into Bitcoin by the referenced block. Verify it independently against the original manifest.

> Your locally signed Spacerocks COA remains the authenticity and offline-verification record. The `.ots` file adds evidence that the exact manifest digest existed by the Bitcoin timestamp.

Required limitations:

- A pending proof has been submitted to one or more OpenTimestamps calendars but is not yet Bitcoin-confirmed.
- A completed proof is included through an aggregate OpenTimestamps commitment.
- OpenTimestamps calendars aggregate many document commitments using Merkle trees.
- The customer generally does not receive a unique Bitcoin transaction.
- The proof establishes an existence-before time, not an exact creation time.
- The proof does not establish authorship, ownership, identity, provenance truth, or specimen authenticity.
- The original `manifest.json` and corresponding `.ots` proof are both required for normal verification.

Claims to avoid:

- "Your certificate is stored on Bitcoin."
- "Your entire COA is on the blockchain."
- "Your certificate has its own Bitcoin transaction."
- "Blockchain proves this meteorite is authentic."
- "The timestamp proves ownership."

## 6. System Components

| Component | Responsibility |
| --- | --- |
| GitHub Pages application | Generate the local COA, calculate the exact manifest digest, request checkout, and display order status |
| Railway FastAPI service | Validate requests, create orders, create Stripe Checkout Sessions, expose status and proof endpoints |
| Stripe Checkout | Collect payment on a Stripe-hosted page |
| Stripe webhook endpoint | Verify payment events and authorize fulfillment |
| Railway Postgres | Store immutable order bindings, events, job state, proof bytes, and current/future delivery state |
| Timestamp worker | Submit exact digest bytes to multiple OpenTimestamps calendars |
| Upgrade worker | Upgrade pending proofs and verify Bitcoin attestations |
| Future transactional email provider | Send payment, pending, confirmed, failure, and recovery notifications after sender approval |
| Off-provider backup | Preserve encrypted database/proof copies outside Railway |

## 7. End-to-End Customer Flow

This is the gated target flow, not a currently runnable provider flow. In particular, public calendar transport, production verification, email sending, and the `delivered` transition remain unavailable.

1. The browser creates and signs the deterministic COA manifest.
2. The browser calculates the exact SHA-256 of the final `manifest.json` bytes.
3. The customer chooses the optional managed Bitcoin timestamp service.
4. The frontend sends only the certificate reference, normalized manifest digest, and required checkout contact fields to FastAPI.
5. FastAPI validates the request and creates an immutable order in Postgres.
6. FastAPI generates a high-entropy status/download token and stores only its hash.
7. FastAPI creates a Stripe Checkout Session using a server-controlled product, Price ID, amount, currency, and quantity.
8. Stripe metadata carries only the opaque internal order ID.
9. The customer pays on Stripe's hosted page.
10. Stripe redirects the browser to a non-authoritative order status page.
11. Stripe sends a signed webhook to FastAPI.
12. FastAPI verifies the raw webhook body, signature, endpoint secret, timestamp tolerance, mode, order metadata, amount, currency, line item, and payment status.
13. FastAPI records the Stripe event idempotently and enqueues a durable timestamp job.
14. The webhook returns a successful response quickly after durable persistence.
15. A worker converts the 64-character hexadecimal digest into the original 32 digest bytes.
16. The worker creates a detached OpenTimestamps proof for those digest bytes without hashing the hex string or hashing the digest again.
17. The worker submits to multiple independent public calendars.
18. The initial pending `.ots` proof is preserved immediately.
19. The status page reports that calendar submission is complete and Bitcoin confirmation is pending.
20. A scheduled worker periodically upgrades the proof.
21. The verifier confirms that the upgraded proof targets the exact original digest.
22. The service verifies the Bitcoin attestation according to the selected confirmation policy.
23. The final proof bytes, checksum, target digest, block metadata, and verification result are preserved, and fulfillment becomes `bitcoin_verified`.
24. A separate durable bundle job persists the verified download bundle and transactional outbox record. During the interval after verification and before bundle persistence, state is `bitcoin_verified` but `proof_available` is false.
25. The customer downloads the separate timestamp proof package through authenticated access after bundle readiness.
26. Without a sender the order remains `bitcoin_verified`; a future approved sender performs an audited transition to `delivered`.

## 8. Exact Digest Contract

The checkout request must use a lowercase 64-character SHA-256 hexadecimal value.

The server must:

- Reject malformed or non-SHA-256 input.
- Normalize the value once before creating the order.
- Freeze the value before creating Checkout.
- Store the exact digest with the order.
- Never allow the digest to change after Checkout creation.
- Convert the value to 32 bytes before creating the OpenTimestamps detached proof.
- Never timestamp the UTF-8 text representation of the hash.
- Never hash the digest a second time before constructing the detached proof.
- Confirm from the completed `.ots` proof that its target digest equals the stored digest.

The service timestamps a customer-supplied digest. It does not independently establish that the digest belongs to a genuine or truthful COA.

A future validated-request mode could use a signed request envelope containing:

- Schema version
- Certificate ID
- Manifest SHA-256
- Public-key fingerprint
- One-time nonce
- Issuer signature

That mode is not part of the initial paid service.

## 9. Order State Dimensions

Payment and fulfillment are separate dimensions in the current contract.

| Payment state | Meaning |
| --- | --- |
| `checkout_open` | Order exists and Checkout has not completed |
| `processing` | Stripe reports a payment method that has not settled |
| `paid` | Payment is confirmed and fulfillment can begin |
| `failed` | Payment failed or an asynchronous method failed |
| `expired` | Checkout expired before payment |
| `refunded` | Commercial order was refunded; timestamp evidence is retained |
| `disputed` | Stripe dispute requires review |

| Fulfillment state | Meaning |
| --- | --- |
| `awaiting_payment` | Fulfillment is not authorized |
| `queued` | Paid work is durably queued |
| `stamping` | A worker is creating or submitting the proof |
| `calendar_pending` | A pending proof exists; Bitcoin attestation is not final |
| `bitcoin_verified` | Exact digest and Bitcoin attestation passed the configured verification policy |
| `delivered` | Reserved for a future audited sender transition after verified delivery; current code has no sender and does not enter this state |
| `manual_review` | Automated processing refuses to continue; current operator tooling cannot restore fulfillment without a future audited state transition |

State transitions must be monotonic and idempotent. Refunds and disputes do not erase an already-created timestamp.

Status projection and artifact readiness are separate from durable historical rows:

- `stamping` always reports `proof_available=false` and suppresses calendar-submission and Bitcoin-verification timestamps, even if stale/historical artifact rows exist.
- `calendar_pending` may expose the current pending proof and calendar-submission time, but never a Bitcoin-verification time.
- `bitcoin_verified` exposes verified state and its verification time before the separate bundle job necessarily finishes. `proof_available` remains false until the matching current verified bundle is durably persisted and validated, then becomes true without changing fulfillment state.
- `manual_review` reports no downloadable proof. Historical proof, verification, or bundle rows remain evidence but cannot override the current refusal state.
- No sender exists, so current runtime remains `bitcoin_verified`; it does not use bundle readiness as a substitute for `delivered`.

## 10. Stripe Controls

Required controls:

- Product, Price ID, currency, amount, and quantity are controlled by the server.
- The frontend never chooses the amount.
- Postgres remains authoritative for the digest and certificate binding.
- Checkout metadata contains only an opaque order ID.
- Stripe webhook verification uses the untouched raw request body.
- Invalid or stale signatures are rejected before processing.
- Browser redirects never authorize fulfillment.
- Stripe event IDs have a database uniqueness constraint.
- Checkout Session, PaymentIntent, order, and fulfillment identifiers are semantically deduplicated.
- The service retrieves current Stripe objects rather than depending on event order.
- Webhooks return `2xx` only after required state has been durably persisted.
- Complex timestamp work never runs inside the webhook request.
- Sandbox and live keys, objects, endpoint secrets, and environments remain separate.
- Checkout provider calls occur only after a durable idempotency reservation is committed. A processing/grace lease is held for 5-300 seconds (60 seconds by default); concurrent retries may receive HTTP `425` and no token until the lease expires.
- Retries must reuse the identical request and idempotency key. After lease expiry, the server reuses the frozen provider idempotency key; it does not create a second mutable order binding.

Required event handling may include:

- `checkout.session.completed`
- Asynchronous payment success and failure if delayed methods are enabled
- Checkout expiration
- Refund creation, updates, failures, and completion
- Dispute creation and updates

The initial beta should accept instant card methods only unless delayed-payment behavior has been fully tested.

## 11. Worker and Job Controls

FastAPI `BackgroundTasks`, process memory, or an in-process scheduler are not sufficient for paid fulfillment.

The service requires:

- A durable Postgres job table or production queue.
- Row locking or leases for job claims.
- Retry counts and attempt history.
- Exponential backoff with jitter.
- Idempotent local stamping/upgrading state, with documented at-least-once same-digest external submission across the calendar-acceptance/local-append crash window.
- Crash recovery.
- Dead-letter or manual-review handling for actual retry exhaustion or unsafe states.
- Operational replay commands.
- Alerts for exhausted jobs.

Calendar acceptance cannot be committed atomically with the local Postgres proof append. If a valid proof was appended before a crash, replay detects and preserves it without resubmission. If a calendar accepted the immutable digest but the process crashed before local append, the service has no durable response to detect and must recover by at-least-once resubmission of the same 32-byte digest. This may create repeated calendar commitments for the same digest; it is neither exactly-once submission nor a unique Bitcoin transaction guarantee.

## 12. OpenTimestamps Lifecycle

The worker should:

- Submit to multiple public calendars.
- Preserve every returned pending attestation.
- Store the initial proof before scheduling upgrades.
- Retry upgrades without replacing historical proof versions.
- Treat an already-valid proof awaiting Bitcoin as ordinary pending; retry calendar transport failures and move unsafe/exhausted errors to manual review, never Bitcoin confirmation.
- Verify the proof target against the stored digest.
- Record the Bitcoin block height, block hash, block time, and chosen confirmation policy.
- Reverify before any future delivery transition.

Ordinary confirmation pending is not a retry failure. The implemented worker completes the current upgrade job and schedules a durable successor six hours later. This successor polling continues beyond a short retry window and does not dead-letter or move to manual review merely because confirmation remains pending. Maximum polling duration, retention, customer escalation, and eventual disposition are unresolved live-policy gates.

Public calendars do not provide a commercial service-level agreement. Confirmation can take hours or occasionally longer because commitments are batched.

For the strongest independent production verification, operate or access a controlled pruned Bitcoin Core node. Public block explorers are convenient but less independent. The exact production verification policy remains a deferred decision.

## 13. Proof Deliverable

The paid service should return a separate proof bundle rather than altering the original COA ZIP.

Suggested structure:

```text
<CERTIFICATE-ID>-bitcoin-timestamp/
|-- README-FIRST.txt
|-- manifest.json.ots
|-- timestamp-receipt.json
|-- verification-instructions.txt
`-- sha256sums.txt
```

`timestamp-receipt.json` should contain only non-sensitive verification metadata, such as:

- Receipt schema version
- Opaque order reference
- Certificate reference
- Target manifest SHA-256
- Proof SHA-256
- Proof byte length
- Current proof state
- Calendar submission time
- Bitcoin block height, hash, and time when confirmed
- Verification method
- Verification time
- Service version

The receipt must not contain card data, Stripe secrets, private keys, specimen photographs, addresses, or unnecessary personal information.

Every raw `.ots` proof version must contain 1 through 262,144 bytes. The parser, in-memory proof type, Postgres constraint, and receipt contract enforce the same maximum. Proof versions and their checksums are append-only; upgrades append a new version and never rewrite historical proof bytes.

The highest valid proof version is the current cryptographic artifact, but downloadable-artifact readiness is a separate projection. A pending download is generated from a selected latest version and returned only after rechecking that the authorization token, immutable order binding, `calendar_pending` state, and selected latest metadata are still current. A `bitcoin_verified` order can report `proof_available=false` while the separate bundle job is pending; verified download becomes available only after a bundle bound to the current verified proof version is durably persisted and its length/checksum validate. `stamping` suppresses proof and timestamp projection, and `manual_review` suppresses download even if historical proof, verification, or bundle rows remain durable. Historical artifacts are evidence, not permission to project an obsolete state.

## 14. Security and Privacy Controls

Required controls:

- Never upload or log private signing keys.
- Never upload or log specimen photographs.
- Never upload the complete COA package.
- Do not upload the manifest unless a future customer-visible validation mode explicitly requires it.
- Treat the digest as potentially pseudonymous rather than automatically anonymous.
- Use at least 128 bits of random status/download token material.
- Store only a cryptographic hash of each order token.
- Support token rotation or revocation.
- Do not put tokens in logs, analytics, email subjects, or referrer URLs.
- Disable third-party analytics on status and download pages.
- Set a restrictive referrer policy.
- Rate-limit checkout, status, webhook, and download endpoints.
- Apply request body and field-size limits.
- Do not treat CORS as authentication.
- Use separate staging and production secrets.
- Sanitize logs and exceptions.
- Rotate secrets after suspected exposure.
- Preserve payment consent, fulfillment, email, and download evidence for disputes.
- Define and enforce a data-retention schedule.

## 15. Data to Retain

Minimum operational data:

- Opaque internal order ID
- Certificate reference
- Exact manifest digest
- Stripe Checkout Session and PaymentIntent references
- Payment state, amount, currency, product version, and mode
- Stripe event IDs required for deduplication
- Customer email required for fulfillment and accounting
- Hashed status/download token
- OpenTimestamps proof versions
- Proof checksums and byte lengths
- Calendar and Bitcoin verification metadata
- State-transition and job-attempt history
- Consent, policy version, delivery, refund, and dispute evidence

Data not to retain:

- Card numbers or payment credentials
- COA private keys
- Encrypted issuer key backups
- Specimen photographs
- Full COA ZIP files
- Unnecessary provenance or buyer details
- Raw status/download tokens

## 16. Suggested API

```text
POST /v1/checkout
POST /v1/webhooks/stripe
GET  /v1/orders/status
GET  /v1/orders/proof
POST /v1/orders/rotate-token
GET  /health/live
GET  /health/ready
```

The checkout endpoint creates an immutable order and returns a Stripe-hosted Checkout URL.

Every order status, proof, and token-rotation request uses the fixed path shown above and requires `Authorization: Bearer <status-token>`. The status token must never appear in a URL path, query string, fragment, referrer, analytics event, or log.

The status endpoint returns only customer-safe state, timing, and current artifact-readiness information. In particular, `bitcoin_verified` may briefly report `proof_available=false` until the separate verified-bundle job persists the matching bundle; `stamping` reports no proof or calendar/Bitcoin timestamps.

The proof endpoint returns only the current state-appropriate artifact when bearer authorization succeeds and artifact readiness allows download. Token rotation revokes the old bearer token and returns the replacement only in the authenticated response body.

## 17. Account Setup - Sandbox

### 17.1 GitHub

- [ ] Confirm administrative access to `rayborg/spacerocks-coa`.
- [ ] Enable GitHub two-factor authentication.
- [ ] Securely retain GitHub recovery codes.
- [ ] Create a separate `spacerocks-coa-service` repository when implementation begins.
- [ ] Grant Railway's GitHub App access only to the backend repository.
- [ ] Do not create a personal access token for normal Railway deployment.

Official documentation:

- [GitHub two-factor authentication](https://docs.github.com/en/authentication/securing-your-account-with-two-factor-authentication-2fa/configuring-two-factor-authentication)
- [Managing installed GitHub Apps](https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps)

### 17.2 Railway

- [ ] Register or sign in at [railway.com/login](https://railway.com/login), preferably through GitHub.
- [ ] Enable Railway MFA and preserve recovery codes.
- [ ] Use the trial for initial sandbox work if available.
- [ ] Wait to authorize repository access until the backend repository exists.
- [ ] Authorize only the required repository.
- [ ] Create separate staging and production environments.
- [ ] Add a Railway Postgres service.
- [ ] Generate a Railway HTTPS domain for sandbox API and webhook access.
- [ ] Keep database traffic on Railway's private network.

Official documentation:

- [Railway accounts](https://docs.railway.com/access/accounts)
- [Railway MFA](https://docs.railway.com/access/multi-factor-authentication)
- [Railway trial](https://docs.railway.com/pricing/free-trial)
- [Railway FastAPI guide](https://docs.railway.com/guides/fastapi)
- [Railway Postgres](https://docs.railway.com/databases/postgresql)
- [Railway public networking](https://docs.railway.com/networking/public-networking)

### 17.3 Stripe Sandbox

- [ ] Register at [dashboard.stripe.com/register](https://dashboard.stripe.com/register).
- [ ] Choose the correct business-origin country carefully.
- [ ] Verify the account email.
- [ ] Enable a passkey, security key, or authenticator-app 2FA.
- [ ] Use Stripe's included test environment or create a dedicated sandbox.
- [ ] Create a sandbox product named for the managed Bitcoin timestamp service.
- [ ] Create a sandbox Price after a test amount is selected.
- [ ] Create a least-privilege sandbox restricted API key when the backend permissions are known.
- [ ] Enter the key directly into Railway staging variables.
- [ ] Register the sandbox webhook URL after the endpoint is deployed.
- [ ] Enter the sandbox `whsec_...` value directly into Railway staging variables.
- [ ] Never send Stripe keys or webhook secrets through chat.

Business verification, payout bank information, and live keys are not required for test payments.

Official documentation:

- [Stripe account activation](https://docs.stripe.com/get-started/account/activate)
- [Stripe testing environments](https://docs.stripe.com/testing-use-cases)
- [Stripe API keys](https://docs.stripe.com/keys)
- [Stripe webhooks](https://docs.stripe.com/webhooks)
- [Stripe hosted Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment?payment-ui=stripe-hosted)

### 17.4 Resend Sandbox

Resend is proposed but still requires final approval as a service dependency.

- [ ] Register at [resend.com/signup](https://resend.com/signup).
- [ ] Enable MFA.
- [ ] Create a Sending-access API key.
- [ ] Copy it once and enter it directly into Railway staging variables.
- [ ] Use `onboarding@resend.dev` only for permitted test-recipient behavior.
- [ ] Exercise delivered, bounced, and complaint test scenarios.

Resend does not have a separate production-approval process. Sending to arbitrary recipients requires a verified sending domain.

Official documentation:

- [Resend API keys](https://resend.com/docs/create-an-api-key)
- [Resend test domain restrictions](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain)
- [Resend test emails](https://resend.com/docs/dashboard/emails/send-test-emails)
- [Resend pricing](https://resend.com/pricing)

### 17.5 OpenTimestamps

No account setup is required.

OpenTimestamps public calendars require no:

- Registration
- Membership
- API key
- Bitcoin wallet
- Bitcoin balance
- Direct transaction fee
- Bitcoin node for initial stamping

Official documentation:

- [OpenTimestamps](https://opentimestamps.org/)
- [OpenTimestamps Python client](https://github.com/opentimestamps/opentimestamps-client)
- [OpenTimestamps JavaScript client](https://github.com/opentimestamps/javascript-opentimestamps)

## 18. Account Setup - Before Live Payments

### 18.1 Stripe Activation

The account owner must personally complete Stripe live onboarding.

Stripe may request information based on country, entity type, ownership, and risk, including:

- Legal or business name
- Business type
- Operating address
- Product and service description
- Website
- Tax identifier
- Business representative information
- Beneficial ownership information
- Identity documents
- Relationship to the business
- Payout bank account
- Settlement currency and payout schedule

The account owner must also:

- [ ] Review Stripe's restricted-business rules.
- [ ] Configure public business and support details.
- [ ] Set a recognizable statement descriptor.
- [ ] Configure payout and dispute notifications.
- [ ] Review localized Stripe pricing and fees.
- [ ] Decide whether Stripe Tax is appropriate.
- [ ] Determine sales-tax, VAT, GST, accounting, and registration duties with qualified advisers.
- [ ] Complete any required PCI documentation.
- [ ] Create separate least-privilege live API credentials.
- [ ] Register a separate live webhook endpoint.
- [ ] Store the separate live webhook signing secret in Railway production.

Standard Stripe Checkout handles payment processing but does not automatically assume every tax, accounting, refund, dispute, or legal obligation. Stripe Managed Payments may provide merchant-of-record services for eligible businesses, but eligibility for this service must be evaluated separately.

Official documentation:

- [Stripe account checklist](https://docs.stripe.com/get-started/account/checklist)
- [Stripe website checklist](https://docs.stripe.com/get-started/checklist/website)
- [Stripe payouts](https://docs.stripe.com/payouts)
- [Stripe Tax registration](https://docs.stripe.com/tax/registering)
- [Stripe security and PCI](https://docs.stripe.com/security/guide)
- [Stripe restricted businesses](https://stripe.com/legal/restricted-businesses)

### 18.2 Customer Policies

Before live Checkout, publish and approve:

- [ ] Terms of service
- [ ] Privacy policy
- [ ] Refund and cancellation policy
- [ ] Delivery and confirmation timing
- [ ] Explanation of pending and confirmed proof states
- [ ] Explanation of what the Bitcoin proof proves
- [ ] Explanation of what the proof does not prove
- [ ] Support email and response expectations
- [ ] Calendar or Bitcoin delay policy
- [ ] Service-failure remedy
- [ ] Data-retention schedule
- [ ] Dispute-handling policy

Professional tax and legal review should be obtained for the relevant jurisdictions. This plan is not legal advice.

### 18.3 Railway Production

- [ ] Add a billing method and accurate billing information.
- [ ] Select a sustainable Railway plan.
- [ ] Configure usage alerts.
- [ ] Configure an acceptable spending limit.
- [ ] Select a production region.
- [ ] Configure health checks and restart behavior.
- [ ] Enable scheduled Postgres backups.
- [ ] Confirm deployment and billing notifications reach a monitored address.
- [ ] Test a database restore into a clean environment.
- [ ] Configure an encrypted off-provider backup.

Railway's published plans and prices can change. Review current information before launch:

- [Railway plans](https://docs.railway.com/pricing/plans)
- [Railway cost controls](https://docs.railway.com/pricing/cost-control)
- [Railway backups](https://docs.railway.com/volumes/backups)
- [Railway production checklist](https://docs.railway.com/overview/production-readiness-checklist)

### 18.4 Resend Production

- [ ] Own or purchase a domain and retain DNS access.
- [ ] Add a dedicated sending subdomain such as `updates.example.com`.
- [ ] Add the exact DKIM, SPF, and Return-Path DNS records supplied by Resend.
- [ ] Add and monitor DMARC.
- [ ] Choose the visible sender name.
- [ ] Choose the `From` and `Reply-To` addresses.
- [ ] Create a production Sending-access API key restricted to the verified domain.
- [ ] Enter the key directly into Railway production variables.
- [ ] Configure and verify Resend webhook signatures if bounce/delivery events are consumed.
- [ ] Monitor bounces, complaints, quotas, and sending reputation.

Official documentation:

- [Add and verify a Resend domain](https://resend.com/docs/add-a-domain)
- [Resend DMARC guide](https://resend.com/docs/dashboard/domains/dmarc)
- [Resend sender addresses](https://resend.com/docs/knowledge-base/how-do-I-create-an-email-address-or-sender-in-resend)
- [Resend webhook verification](https://resend.com/docs/webhooks/verify-webhooks-requests)

### 18.5 Bitcoin Verification Policy

No Bitcoin account or wallet is required.

Before live launch, choose one verification model:

1. Operate a controlled pruned Bitcoin Core node.
2. Use a managed Bitcoin RPC provider with documented trust assumptions.
3. Use multiple public block data providers and clearly disclose that verification is less independent.

The service must not label a proof Bitcoin-confirmed until the selected verification policy passes.

## 19. Secrets and Configuration

Secrets must be entered directly into Railway's Variables dashboard. They must never be sent through chat, committed to Git, placed in screenshots, or exposed in frontend code.

| Value | Secret? | Environment |
| --- | ---: | --- |
| Stripe sandbox restricted key | Yes | Railway staging only |
| Stripe live restricted key | Yes | Railway production only |
| Stripe sandbox webhook secret | Yes | Railway staging only |
| Stripe live webhook secret | Yes | Railway production only |
| Stripe sandbox Price ID | No, internal configuration | Railway staging |
| Stripe live Price ID | No, internal configuration | Railway production |
| Railway `DATABASE_URL` | Yes | Railway reference variable |
| Railway database password | Yes | Railway-managed reference |
| Resend staging API key | Yes | Railway staging only |
| Resend production API key | Yes | Railway production only |
| Resend webhook signing secret | Yes | Corresponding Railway environment |
| Backend token-hashing pepper | Yes | Separate staging and production values |
| Backend encryption/signing secret, if introduced | Yes | Separate staging and production values |
| Frontend origin | No | Environment-specific configuration |
| API URL | No | Frontend configuration |
| Public calendar URLs (future) | No | No current runtime variable; only after pinned-public-IP TLS/SNI transport and composition review |
| Status/download token | Yes to the customer | Store only its hash in Postgres |
| MFA recovery codes | Yes | Offline password manager or secure recovery storage |

If any secret is exposed, revoke or rotate it immediately. Concealing it afterward is not sufficient.

## 20. User-Only Responsibilities

The account owner must personally:

- Accept third-party terms and contracts.
- Choose the Stripe account country and entity type.
- Complete Stripe identity and business verification.
- Provide payout bank and tax information directly to Stripe.
- Authorize Railway's GitHub App.
- Choose and pay for Railway services.
- Buy or control any custom domain.
- Change DNS records.
- Approve product claims, price, refund policy, support policy, and retention policy.
- Obtain tax, accounting, or legal advice.
- Enter and rotate secrets directly in service dashboards.
- Secure passwords, MFA methods, and recovery codes.
- Monitor payments, payouts, refunds, disputes, invoices, and account-verification requests.

These credentials and identity documents must never be provided to the coding agent through chat.

## 21. Engineering Responsibilities

Engineering scope includes the following. Phase 0 implements portions of this list as described in the current implementation boundary; provider-backed and live work remains gated:

- Creating the backend repository.
- Building FastAPI endpoints.
- Designing Postgres schema and migrations.
- Implementing immutable order creation.
- Implementing Stripe Checkout Session creation.
- Implementing raw-body webhook verification.
- Implementing idempotent event processing.
- Implementing durable worker claims and retries.
- Integrating OpenTimestamps digest stamping.
- Preserving and upgrading proof versions.
- Implementing Bitcoin attestation verification policy.
- Building status and proof-download pages.
- Building transactional email templates.
- Adding monitoring and recovery commands.
- Adding automated unit, integration, payment, failure, and security tests.
- Updating the GitHub Pages frontend.
- Preparing Railway service definitions and environment-variable names.
- Documenting deployment, backup, recovery, refund, and incident procedures.

## 22. Refund and Dispute Policy Requirements

The final policy must define when fulfillment begins:

- When payment settles
- When a worker starts
- When a calendar accepts the commitment
- When Bitcoin attestation becomes available

Recommended behavior:

- If payment succeeds but no initial pending proof can be created within the approved operational limit, retry transport failures and then move unsafe/exhausted work to manual review under the approved refund policy. This does not apply to an already-valid ordinary pending proof, which uses six-hour successor polling.
- If payment is refunded before stamping, cancel pending work where possible.
- If calendar submission has already occurred, retain the timestamp because it cannot be withdrawn.
- A refund changes the commercial order state but does not erase cryptographic evidence.
- A prolonged Bitcoin delay should trigger support escalation rather than a false failure or confirmation.
- Dispute handling should preserve checkout, consent, delivery, email, download, and service-state evidence.

## 23. Monitoring and Operations

Required alerts:

- Paid order not stamped within the operational threshold
- Pending proof older than the expected range
- Calendar submission failures
- Proof target mismatch
- Bitcoin verification failure
- Job retries exhausted
- Webhook signature or processing failures
- Email bounce or complaint
- Database backup failure
- Off-provider backup failure
- Restore drill failure
- Refund or dispute event
- Railway budget or storage threshold

Required runbooks:

- Replay a safe Stripe event
- Resume a failed timestamp job
- Upgrade a pending proof manually
- Reverify a completed proof only under an approved verifier/reorganization policy and record each request/result in typed append-only history
- Rotate an exposed status token
- Rotate Stripe, Railway, Resend, and backend secrets
- Reconcile Postgres orders with Stripe
- Restore Postgres and proof data
- Issue a refund
- Respond to a dispute
- Notify customers of a service incident

## 24. Sandbox Acceptance Gates

Live payments remain blocked until all applicable gates pass.

1. A known manifest fixture produces the expected SHA-256.
2. The generated `.ots` targets the exact digest without double hashing.
3. No private key, photograph, manifest, or COA ZIP reaches the service.
4. Stripe success, decline, 3DS, abandoned Checkout, expiration, refund, and dispute scenarios have correct states.
5. Invalid webhook signatures and modified raw bodies cannot trigger fulfillment.
6. Wrong sandbox/live mode, amount, currency, product, order metadata, or payment state cannot trigger fulfillment.
7. Duplicate, concurrent, and out-of-order webhooks create one durable order/fulfillment job stream; this does not override documented at-least-once same-digest calendar resubmission after an acceptance-before-append crash.
8. Worker termination at each processing stage recovers without losing or corrupting proof data; a crash after calendar acceptance but before local proof append is expected to at-least-once resubmit the same immutable digest.
9. Multiple-calendar submission succeeds.
10. Complete calendar outage remains safely pending and alerts operations.
11. Ordinary pending uses durable six-hour successor polls without consuming a short retry/dead-letter budget; outage/error retries remain bounded, and approved retention/escalation limits are documented.
12. Confirmed state requires exact-digest verification and the selected Bitcoin confirmation/reorganization policy.
13. Wrong-digest, corrupt, truncated, and pending proofs cannot be delivered as confirmed.
14. Status and proof tokens resist enumeration and are absent from logs and analytics.
15. An approved sender, email delivery/bounce handling, repeat download, and support lookup work without relying on the browser redirect; until then orders remain `bitcoin_verified`, not `delivered`.
16. Database and proof backups restore successfully into a clean test environment.
17. Stripe reconciliation identifies missed or delayed events.
18. Monitoring alerts and manual recovery procedures have been exercised.
19. A mainnet-calendar pilot using synthetic random digests completes end to end.
20. Price, fees, taxes, refund reserve, support cost, customer disclosures, and retention policy are approved.

## 25. Phased Rollout

### Phase 0 - Local and Deterministic Tests

- Implement database, state-machine, webhook, and worker tests.
- Use Stripe sandbox fixtures.
- Use mocked calendars or an OpenTimestamps test environment.
- Prove exact digest semantics.

### Phase 1 - Sandbox with Public Calendars

- Use Stripe sandbox.
- Submit synthetic random digests to public OpenTimestamps calendars.
- Exercise real delayed proof upgrades.
- Test email and status delivery.
- Complete backup and restore drills.

### Phase 2 - Invite-Only Live Beta

- Choose a price.
- Activate Stripe live mode.
- Limit payment methods to instant cards.
- Enforce strict volume and rate limits.
- Monitor every order manually in addition to automation.
- Use conservative customer claims.

### Phase 3 - General Availability

- Open access only after sustained successful beta operation.
- Confirm refund and dispute handling.
- Confirm backup restores and incident procedures.
- Publish support expectations and service status.
- Review confirmation timing and failure metrics.

## 26. Estimated Service Costs

Costs must be rechecked before implementation or launch.

| Service | Expected cost category |
| --- | --- |
| Stripe | Localized payment-processing, international card, conversion, dispute, tax, and optional product fees |
| Railway | Monthly plan plus compute, Postgres, network, volume, and backup usage |
| Resend | Free allowance or paid transactional email plan depending on volume |
| OpenTimestamps calendars | Free public infrastructure with no commercial SLA |
| Domain | Annual registrar and optional DNS costs |
| Off-provider backup | Storage, request, and network usage |
| Bitcoin verification | Node hosting/storage or managed RPC/API costs if selected |
| Professional review | Accounting, tax, privacy, policy, and legal review as applicable |

Current published Railway and Resend prices can change and must not be hard-coded into customer economics without rechecking their official pages.

## 27. Recurring Account Responsibilities

### Stripe

- Review payments, payouts, refunds, disputes, fraud warnings, and negative balances.
- Respond to disputes within Stripe deadlines.
- Keep legal, bank, support, website, and statement information current.
- Complete ongoing PCI tasks shown in the Dashboard.
- File and remit applicable taxes or confirm the selected provider handles them.
- Rotate API and webhook secrets periodically.
- Monitor Stripe API and version changes.

### Railway and GitHub

- Monitor usage, invoices, service health, deployments, storage, and database metrics.
- Test restores periodically.
- Maintain database upgrades, indexing, and recovery procedures.
- Review GitHub App access and collaborators.
- Keep MFA and recovery methods current.
- Remove unused access promptly.

### Resend

- Monitor quota, billing, delivery logs, suppressions, bounces, complaints, and DNS health.
- Maintain valid recipient addresses and transactional-only sending behavior.
- Rotate API keys periodically.
- Monitor SPF, DKIM, and DMARC after DNS changes.

### OpenTimestamps

- Preserve every original digest and `.ots` proof.
- Upgrade pending proofs.
- Submit through multiple calendars.
- Monitor public-calendar availability.
- Periodically reverify completed proofs.
- Keep clients updated while preserving old proof compatibility.

## 28. Deferred Decisions

The following decisions remain required before the corresponding provider-backed sandbox work or any live service begins:

- [ ] Approve Resend or select another transactional email provider.
- [ ] Confirm the Stripe account country and general entity type.
- [ ] Identify the domain and DNS provider, if one exists.
- [ ] Select the public support email address.
- [ ] Decide the production Bitcoin verification model.
- [ ] Select an off-provider backup service.
- [ ] Define the refund and service-failure policies.
- [ ] Define expected support response time.
- [ ] Define data-retention periods.
- [ ] Decide the sandbox test price.
- [ ] Decide the live launch price after sandbox measurement.
- [ ] Decide whether the service timestamps any customer-supplied digest or only requests generated by Spacerocks COA Studio.
- [ ] Decide whether a future signed request-envelope validation mode is required.

## 29. Immediate Project Priority

Phase 0 local and deterministic implementation was explicitly authorized and is now present on the feature branch. Continue validating that code and correcting the foundational browser-only COA without treating Phase 0 as an operational service.

Do not enable provider-backed public-calendar operation, transactional email, live payment mode, or public deployment until the applicable sandbox gates, account-owner decisions, policies, backup/restore controls, monitoring, and explicit authorization are complete. Phase 1 and every live phase remain blocked.

## 30. Primary Official Sources

- [Stripe hosted Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment?payment-ui=stripe-hosted)
- [Stripe webhook security and behavior](https://docs.stripe.com/webhooks)
- [Stripe webhook signatures](https://docs.stripe.com/webhooks/signature)
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
- [Stripe refunds](https://docs.stripe.com/refunds)
- [Stripe disputes](https://docs.stripe.com/disputes)
- [Stripe integration security](https://docs.stripe.com/security/guide)
- [Railway accounts](https://docs.railway.com/access/accounts)
- [Railway production checklist](https://docs.railway.com/overview/production-readiness-checklist)
- [Railway backups](https://docs.railway.com/volumes/backups)
- [Railway plans](https://docs.railway.com/pricing/plans)
- [Resend domain setup](https://resend.com/docs/add-a-domain)
- [Resend API keys](https://resend.com/docs/create-an-api-key)
- [Resend webhook verification](https://resend.com/docs/webhooks/verify-webhooks-requests)
- [OpenTimestamps](https://opentimestamps.org/)
- [OpenTimestamps Python client](https://github.com/opentimestamps/opentimestamps-client)
- [OpenTimestamps JavaScript client](https://github.com/opentimestamps/javascript-opentimestamps)
