# Timestamp Service Threat Model

## Scope and status

This model covers the Phase 0 optional frontend, FastAPI service, Postgres data, durable worker/operator paths, Stripe test integration boundary, and OpenTimestamps proof lifecycle. Current execution is deterministic/local sandbox only. Public calendar parsing/fan-out exists, but no runtime-selectable transport exists: settings/composition cannot enable it and the default transport refuses operation pending pinned-public-IP TLS/SNI review. Production Bitcoin verification, email delivery, live payments, and deployment remain release blockers.

The security objective is limited: preserve an immutable binding between a customer-supplied certificate reference, the SHA-256 of the exact final `manifest.json` bytes, payment authorization, and a separately downloadable OpenTimestamps proof. The service does not prove certificate truth, specimen authenticity, ownership, authorship, human identity, or that signature bytes existed by the Bitcoin block.

## Assets

- exact 32-byte manifest digest and immutable certificate/order binding;
- private bearer recovery tokens and token-hashing peppers;
- fulfillment email, consent, policy versions, and payment/order evidence;
- Stripe keys, webhook secrets, Price configuration, and canonical provider references;
- Postgres credentials, rows, migrations, durable jobs, leases, and append-only audit evidence;
- pending and upgraded `.ots` bytes, proof checksums, target binding, and Bitcoin verification metadata;
- source, dependency locks, CI integrity, deployment identity, GitHub App scope, and operator access; and
- availability and truthful distinction among payment, pending, verified, future delivered, refunded, disputed, and manual-review states.

Private COA signing keys, passphrases, photographs, full manifest contents, COA ZIPs, addresses, provenance data, and card credentials must stay outside this system.

## Trust boundaries and data flow

1. Browser to static frontend: all `VITE_*` configuration and shipped code are public. Local COA generation is the primary boundary.
2. Browser to API over HTTPS: checkout sends only certificate reference, manifest digest, email, and consent. Status token travels only in the `Authorization` header.
3. Browser to Stripe-hosted Checkout: card data goes directly to Stripe. The browser return is non-authoritative.
4. Stripe webhook to API: the raw body crosses an untrusted public boundary and requires signature, tolerance, mode, metadata, amount, currency, line-item, payment-state, and canonical object checks.
5. API/worker/operator to Postgres: database state authorizes durable work and retains proof/payment evidence. Migrations and operator commands are privileged serialized paths.
6. Worker to calendars and Bitcoin verification source: public transport and production verification are unavailable in Phase 0. Future calendar responses are non-transactional external input and remain untrusted until structural/exact-target checks and an approved confirmation/reorganization policy succeed.
7. Outbox to email provider: only durable outbox records exist. A future sender creates a new PII/provider boundary requiring separate approval.
8. CI/source to deployment: pull-request code is untrusted. CI has read-only repository permission, no service secrets, no deployment step, and uses synthetic/mocked providers.

## Threats and controls

| Category | Threat | Implemented or required controls | Residual risk / gate |
| --- | --- | --- | --- |
| Spoofing | Attacker guesses or steals a status token | High-entropy bearer token, only hash stored, header-only transport, no URL/referrer/log use, rotation/revocation, rate limits, `no-store` | Bearer possession grants access; customer endpoint compromise and support mishandling remain risks |
| Spoofing | Forged payment event authorizes work | Signed raw webhook, timestamp tolerance, canonical Stripe object retrieval, test/live mode and object checks | Stripe test end-to-end exercise is not complete; live mode is forbidden |
| Spoofing | Forwarded address spoof bypasses limits | Trust `X-Forwarded-For` only from exact configured proxy IPs | Railway proxy topology must be verified; never trust wildcard networks |
| Tampering | Digest hex is hashed as text or hashed twice | Strict lowercase SHA-256 format, decode once to 32 bytes, detached proof, exact-target validation, known fixture | Public client/library compatibility and production verifier remain untested |
| Tampering | Order amount/digest changes after checkout | Server-controlled Price/amount/currency/quantity, immutable binding, database constraints/triggers, idempotency | Migration/restore drills and provider reconciliation remain required |
| Tampering | Proof is corrupt, truncated, oversized, replaced, or mismatched | Shared 262,144-byte raw-proof cap, checksum/length, append-only versions, exact target validation, deterministic bundle and strict receipt states | Off-provider backup and independent production verification are absent |
| Tampering | Historical valid proof is served after state/version changed | Latest-version metadata selection, current-state checks, post-build pending recheck, persisted verified bundle binding, manual-review suppression | Race and restore tests remain required; historical durability is not download authorization |
| Tampering | Verified state is mistaken for immediate bundle readiness, or stamping leaks stale artifact metadata | Status matrix suppresses stamping proof/timestamps and derives verified `proof_available` from the matching persisted bundle | Bundle-job lag is expected; clients and alerts must treat verification and artifact readiness separately |
| Repudiation | Customer/provider disputes consent, payment, or future delivery | Versioned consent, canonical provider references, unique event IDs, append-only transitions, job/outbox/proof evidence | No sender exists; retention, privacy, refund, dispute, delivery, and support policies are not approved |
| Information disclosure | PII, token, or secret leaks through logs/errors | Allowlisted safe request paths, sanitized errors, no access log in service commands, token hashes, response minimization, security headers | Platform/proxy/provider logs need configuration and audit |
| Information disclosure | Browser uploads local COA material | Explicit allowlisted request shape; frontend tests exact keys; API field/size validation | Malicious modified clients can send arbitrary traffic; server must continue rejecting unknown data and avoid body logging |
| Information disclosure | Digest is treated as anonymous | Documentation classifies digest as potentially pseudonymous; minimize association and retention | Correlation with public records or leaked manifests remains possible |
| Denial of service | Oversized bodies, request floods, expensive downloads | Body limits, endpoint-specific durable rate limits, bounded frontend polling, worker retries/leases, 262,144-byte raw-proof limit | Distributed attacks, storage exhaustion, provider outages, and budget exhaustion require edge controls/alerts |
| Denial of service | Ordinary pending exhausts retries or stops being checked | Each pending result schedules a durable six-hour successor without consuming error attempts | Poll/retention/escalation maximums and successor-gap alerts remain live gates |
| Denial of service | Checkout retries race provider processing or token issuance | Committed processing/grace lease, frozen provider idempotency key, HTTP `425` without token during lease | Client retry discipline and lease-age monitoring are required |
| Repudiation | Calendar accepted a digest but worker crashed before local append | Same immutable digest is frozen and replay preserves an existing local proof | Calendar/local DB cannot transact; at-least-once same-digest resubmission is expected and may create repeated commitments |
| Elevation of privilege | Fixture or live adapters enabled in wrong environment | Fixture requires `APP_ENV=test` plus loaded `pytest`; `stripe_live` rejected; public transport is absent; Stripe test needs explicit gate and test key prefix | Environment-variable control is privileged; deployment review and separation are mandatory |
| Elevation of privilege | CI or deployment integration gains write access | `contents: read`, no CI secrets/deploy, checkout credentials not persisted; separate backend repo and least-privilege GitHub App required | Version-tagged third-party actions and dependency registries remain supply-chain trust |
| Elevation of privilege | Operator replays or mutates wrong record | Opaque/UUID validation, exact confirmation phrase, state eligibility checks, serialized ownership | Commands are powerful; no full RBAC/audit wrapper is implemented |
| Elevation of privilege | Operator forces manual-review or stale verified data back into service | Manual review suppresses proof/download; terminal replay validates then refuses without an audited state transition | Recovery transition is intentionally absent; live reverification history and reorg policy are not complete |

## Cryptographic and claim risks

- SHA-256 collision/preimage resistance and Ed25519 security are assumptions, not business-truth validation.
- The target is `SHA-256(exact final UTF-8 bytes of manifest.json)` exactly once. Reformatting or line-ending changes produce a different target.
- The signature is not itself timestamped. A later-valid signature does not establish that `signature.sig` existed by the referenced block.
- OpenTimestamps normally aggregates many commitments. A customer generally has no unique Bitcoin transaction.
- Calendar submission is at least once across the acceptance-before-local-append crash window. Repeated submission targets the same immutable digest and provides no exactly-once or unique-transaction guarantee.
- A Bitcoin attestation gives an existence-before bound, not exact creation time.
- Calendar acceptance, proof possession, payment, redirect, pending status, or a fixture result is not Bitcoin verification.

## Privacy risks

The minimum checkout data is certificate reference, digest, fulfillment email, and consent. Payment/order IDs, event history, proof lifecycle, outbox/future-delivery, refund, and dispute evidence are retained only under an approved policy. Data minimization can conflict with accounting, disputes, support, and immutable timestamp evidence. Deletion policy must distinguish removable PII from proof commitments that cannot be withdrawn from calendars or Bitcoin.

The status token must never be used as an order lookup value in support communications. Email introduces phishing and account-enumeration risks and remains unimplemented. Third-party analytics are forbidden on status/download views.

## Residual release blockers

- pinned-public-IP TLS/SNI calendar transport implementation and independent review, runtime composition/settings gate, allowlist policy, and multi-calendar outage/resubmission behavior;
- approved independent Bitcoin verification policy/source, confirmation criteria, reorganization handling, and typed append-only history for every repeated reverification;
- Stripe sandbox provider exercise, webhook registration, reconciliation, and failure scenarios;
- email provider selection, sender authentication, bounce/complaint handling, minimized templates, and an audited transition from `bitcoin_verified` to `delivered`; current code has no sender and never reaches `delivered`;
- staging/production isolation, edge rate limiting, verified proxy topology, alerts, and incident exercises;
- encrypted off-provider backup and successful clean restore/reverification drill;
- dependency/action provenance review, vulnerability response, and image scanning policy;
- account-owner approval of price, claims, terms, privacy, six-hour polling duration/escalation, retention/deletion, refund/dispute, support, tax, and legal duties; and
- explicit authorization for each rollout phase. Live payment mode is not implemented.

Review this model whenever a trust boundary, provider, data field, proof state, dependency, deployment platform, or customer claim changes.
