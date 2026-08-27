# Timestamp Service Threat Model

## Scope and status

This model covers the optional production-capable frontend mode, FastAPI API, Neon PostgreSQL target, timestamp and notification workers, Stripe Checkout/webhooks, hardened public-calendar transport, private Bitcoin Core verification, Resend sending/webhooks, proof lifecycle, and operator/deployment paths.

The MVP scope is code-complete, but production is not publicly live and real customer payments are unavailable. A previously deployed Stripe-test sandbox recorded working checkout/refund canaries; those results have not been freshly verified. Code and recovered records do not prove current cloud state or authorize launch.

The security objective is limited to preserving an immutable binding among a customer-supplied certificate reference, SHA-256 of the exact final `manifest.json` bytes, payment authorization, OpenTimestamps proof, Bitcoin evidence, and notification evidence. The service does not establish certificate truth, specimen authenticity, ownership, authorship, issuer identity, provenance, or that signature bytes existed by the Bitcoin block.

## Assets

- exact 32-byte digest and immutable certificate/order binding;
- bearer recovery tokens and token-hashing peppers;
- fulfillment email, consent, policy versions, and payment/order evidence;
- Stripe keys, webhook secrets, Price configuration, and canonical provider references;
- Neon credentials, migrations, rows, jobs, leases, backups, and restore evidence;
- pending/upgraded `.ots` bytes, checksums, bundles, Bitcoin verification and append-only confirmation observations;
- Bitcoin Core RPC credentials and node integrity;
- Resend API/webhook secrets, sender identity, notification attempts, provider IDs, and delivery/bounce/complaint evidence;
- source/dependency/deployment identity, DNS/TLS, GitHub/provider access, and operator authority; and
- truthful distinction among payment, pending, `bitcoin_verified`, email `delivered`, final six-confirmation notice, refund/dispute, and `manual_review`.

Private COA keys, passphrases, photographs, full manifest/ZIP, addresses, provenance fields, and card credentials must remain outside the system.

## Trust boundaries

1. Browser/static frontend: shipped code and every `VITE_*` value are public. Production mode requires non-Phase-0 policies and reviewed HTTPS URLs.
2. Browser/API: checkout sends only certificate reference, digest, email, and consent. Bearer tokens use `Authorization`, never URLs.
3. Browser/Stripe Checkout: Stripe receives card data. Browser return is non-authoritative.
4. Stripe/API webhook: raw public input requires signature/tolerance plus canonical mode, metadata, amount, currency, Price, line-item, and payment-state checks.
5. API/workers/operators/Neon: database state authorizes durable work. Migrations, restore, and operator commands are privileged serialized paths.
6. Timestamp worker/public calendars: DNS and calendar responses are hostile external input. Calendar acceptance is not transactional with local append and is irreversible.
7. Timestamp worker/Bitcoin Core: RPC must be private and authenticated. Node sync, network, canonical block/header, Merkle-root, and tip consistency are checked.
8. Notification worker/Resend API: recipient PII crosses a provider boundary. API acceptance is not delivery.
9. Resend/API webhook: signed public events can change notification state and, for matching initial delivery evidence, fulfillment state.
10. CI/source/deployment/DNS: pull-request code and supply chain are untrusted; deployment, cloud, secret, and DNS access are privileged owner-controlled paths.

## Threats and controls

| Category | Threat | Implemented controls | Residual gate/risk |
| --- | --- | --- | --- |
| Spoofing | Stolen/guessed status token | High entropy, hash-only storage, header transport, rotation, rate limits, `no-store` | Bearer possession grants access; customer/support compromise remains possible |
| Spoofing | Forged Stripe payment | Raw signature verification, tolerance, canonical retrieval, mode/object/amount/currency/Price/payment checks | Fresh sandbox/live canaries and live webhook setup are incomplete |
| Spoofing | Forged Resend delivery | Svix-compatible raw-body HMAC, timestamp tolerance, bounded IDs/body, event dedupe, provider-message binding | Domain/webhook provider setup and live exercises are incomplete |
| Spoofing | Forwarded IP bypass | Trust first forwarded address only from exact configured immediate proxy IP | Production proxy topology must be verified; no wildcard trust |
| Tampering | Digest text is hashed or digest is double-hashed | Strict lowercase SHA-256, decode once to 32 bytes, detached proof, exact-target validation | Public pilot and independent operational review remain gated |
| Tampering | Order/price/digest changes after checkout | Server Price/amount/currency/quantity, immutable DB binding, idempotency, constraints/triggers; isolated Neon migration/trigger tests passed | Production was empty, so customer-data restore and proof-binding comparison remain untested |
| Tampering | Calendar SSRF/DNS rebinding/redirect | Operator HTTPS allowlist, 2+ independent hosts, resolve once, reject any non-global DNS result, pin vetted IP, hostname TLS/SNI, no redirects/proxy env, bounded fan-out/body | Independent review, chosen allowlist, DNS monitoring, and explicit pilot authorization required |
| Tampering | Corrupt/wrong/oversized/stale proof | 262,144-byte cap, structural parsing, target/checksum/length checks, append-only versions, current-state/version/bundle checks | Restore and end-to-end pilot evidence required |
| Tampering | Malicious or stale Bitcoin RPC evidence | Private auth, mainnet/sync checks, canonical hash/header serialization, header hash, Merkle root, confirmations, tip recheck | Node hardening, monitoring, and approved reorg policy required |
| Tampering | Reorganization after verification | Append-only observations; lost/decreased/conflicting evidence raises terminal error and moves to `manual_review` | Recovery policy is intentionally not generic; owner-approved handling remains required |
| Tampering | Email delivery falsely changes fulfillment | Initial notice bound to current proof/bundle, verification, latest observation `>=1`, Resend acceptance, and signed matching delivery event | Provider/DNS exercises and monitoring required |
| Repudiation | Customer/provider disputes consent, payment, proof, or delivery | Versioned consent, canonical references, unique events, state/job/proof/observation/attempt/webhook evidence | Retention, refund/dispute, privacy, support, and legal policies incomplete |
| Information disclosure | Logs expose PII/tokens/secrets/proofs | Safe paths/errors, no access log in command, hash-only tokens, minimal payload/templates, public frontend separation | Platform, proxy, Neon, Stripe, Resend, and node logs need audit |
| Information disclosure | Frontend uploads local COA content | Exact allowlisted request shape and size/field validation | Modified clients remain hostile; body logging stays forbidden |
| Information disclosure | Digest treated as anonymous | Digest documented as potentially pseudonymous | Correlation with public/leaked manifests remains possible |
| Denial of service | Request/storage/provider exhaustion | Body/rate/proof limits, durable leases, bounded timeouts/fan-out/retries, edge-control requirement | Distributed abuse, provider outage, cost/storage exhaustion require alerts |
| Denial of service | Ordinary pending exhausts retries | Six-hour successor scheduling separate from error retry budget | Maximum retention/escalation policy remains owner gate |
| Denial of service | Confirmation/final notice stops | Fifteen-minute successor monitoring until `>=6`; durable outbox leases/retries | Gap, dead-letter, provider quota, and complaint monitoring required |
| Elevation | Wrong environment enables fixture/provider mode | Fixture restricted to pytest/test; explicit test/live gates; production version checks; disabled defaults; mode-specific settings validation | Environment/secret control is privileged and needs deployment review |
| Elevation | Public Bitcoin RPC or broad database access | Documented private topology and least privilege | Private networking and access checks are external launch gates |
| Elevation | Operator forces unsafe recovery | State eligibility, UUID/confirmation phrases, manual-review recovery refusal | Operator RBAC/audit wrapper and reviewed recovery procedure remain needed |
| Supply chain | CI/deployment gains broad access | Private reviewed source, immutable image, CI/review/least privilege required | Provenance, scanning, provider access, and rollback evidence incomplete |

## Confirmation and delivery semantics

- A pending proof, calendar acceptance, Stripe payment, browser redirect, fixture result, email acceptance, or email delivery is not Bitcoin confirmation.
- `bitcoin_verified` requires exact-digest canonical Bitcoin evidence with `>=1` confirmation.
- Bundle creation and initial notification are separate durable work after verification.
- `delivered` means a signature-verified matching Resend `email.delivered` event for the initial notice backed by current proof/bundle and an observation at `>=1`. It does not mean six confirmations.
- The final notice is generated at `>=6`; its delivery adds evidence but no stronger fulfillment state.
- Lost confirmation, decreased count, or immutable evidence conflict fails closed to `manual_review`, which suppresses proof access.

## Privacy and irreversibility

Minimum checkout data is certificate reference, digest, fulfillment email, and consent. Notification templates expose only the opaque order reference and fixed text. Status tokens, digests, certificate data, and proof bytes must not be emailed.

Data minimization conflicts with accounting, disputes, support, append-only evidence, and irreversible calendar/Bitcoin commitments. Policies must distinguish removable PII from public commitments that cannot be recalled by refund, deletion, restore, token revocation, rollback, or account closure. Calendar acceptance-before-append may create repeated commitments of the same digest.

## Residual release blockers

- ongoing Neon backup monitoring and a customer-data restore/reverification drill when production data exists; the empty-production isolated migration passed on 2026-08-27;
- private deployment access, network, logging, proxy, backup, monitoring, incident, cost, and rollback checks;
- independent hardened-calendar review, approved allowlist, explicit synthetic pilot authorization, and end-to-end pilot;
- private synchronized Bitcoin Core provisioning, credential/network controls, confirmation/reorganization policy, and manual-review exercises;
- Resend provider approval, sending-domain DNS, signed webhook, delivery/bounce/failure/complaint tests, reputation monitoring, and support process;
- fresh Stripe-test canaries, live account/Price/restricted key/webhook, tax/accounting/legal decisions, refunds/disputes, and reconciliation;
- production frontend/API DNS and TLS, exact origins/return URLs, policies, support, price, retention/deletion, and customer claims;
- owner-controlled live canaries and explicit invite-only/public launch authorization.

All corresponding modes must remain disabled until these external gates are evidenced and approved. Review this model whenever a provider, trust boundary, setting, data field, state, migration, deployment target, or customer claim changes.
