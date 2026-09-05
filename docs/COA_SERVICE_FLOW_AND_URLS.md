# COA Service Flow and URLs

**Status:** The core COA is local and browser-only. The paid timestamp MVP is code-complete but production is not publicly live and real customer payments are unavailable.

**Last reviewed:** 2026-08-27

## Local COA flow

1. Create/import the issuer-controlled Ed25519 identity in the browser; keep its private key and passphrase local.
2. For an official meteorite, validate its exact code against the bundled LPI Meteoritical Bulletin snapshot and follow the official record link when personal confirmation is needed. For unknown/unlisted material, use **Unclassified** and do not present a suspected type as official.
3. Complete the required specimen/provenance fields and add exact photographs. At least one unmodified original is required; photographs must not be AI-generated or altered.
4. Generate the deterministic manifest and certificate files, hash evidence, sign the exact final manifest bytes, and build the self-contained ZIP locally.
5. Keep the encrypted private-key backup separate. The package contains the public key/signature, never the private key/passphrase.
6. Verify in the browser or packaged offline verifier without any backend.

The optional lookup API reads a bundled, attributed LPI snapshot and makes no request to LPI at runtime. It does not make the application an independent authority.

## Optional paid timestamp flow

This supplemental path begins only after local release and explicit consent:

1. The browser hashes the exact final UTF-8 `manifest.json` bytes with SHA-256.
2. The frontend sends only certificate reference, lowercase digest, delivery email, and consent record to the configured API.
3. The API creates an immutable order and a hashed recovery-token binding.
4. Stripe-hosted Checkout collects payment data; only a verified canonical Stripe webhook authorizes work.
5. The timestamp worker decodes the digest to its exact 32 bytes and submits a detached proof to allowlisted public calendars.
6. The worker retains pending proof versions and upgrades them on six-hour successor jobs.
7. Private Bitcoin Core RPC verifies exact-digest canonical mainnet evidence at `>=1`; fulfillment becomes `bitcoin_verified`.
8. A separate durable job creates the bundle and initial `>=1` confirmation email record.
9. The notification worker sends through Resend. Only its verified matching `email.delivered` webhook can move the order to `delivered`.
10. Fifteen-minute monitoring records confirmation observations and sends the final notice at `>=6`.
11. Lost/decreased/conflicting Bitcoin evidence moves processing to `manual_review`; historical proof and email records do not authorize download.
12. The customer uses bearer-authenticated status/proof routes. A redirect, pending proof, email, or `delivered` state is not a substitute for Bitcoin evidence.

The public calendar, Bitcoin Core, Resend, Stripe live, and production frontend modes exist in code but default disabled and remain owner-gated.

## Recovered state and current gates

A Stripe-test sandbox was previously deployed and checkout/refund canaries were recorded working, but they have not been freshly verified. No production endpoint or public paid service is confirmed. Neon migration and empty-production isolated-branch validation passed on 2026-08-27, including all 270 backend tests; customer-data restore and proof reverification could not be exercised because production contained no application data. Resend/DNS, private deployment checks, calendar pilot authorization, private Bitcoin Core checks, Stripe tax/live webhook, production DNS/TLS, owner live canaries, and explicit launch approval remain blocked.

`timestamp-service/railway.toml` is optional metadata and does not prove any Railway service or Railway Postgres database exists.

## Data boundary

The COA form, photographs, complete ZIP, manifest contents, signing key, passphrase, encrypted key backup, physical address, provenance fields, and card data remain outside the timestamp API. Stripe hosts payment collection. The service receives certificate reference, digest, email, and consent only. The digest is potentially pseudonymous, not guaranteed anonymous.

## Route and URL register

| URL, variable, or route | Status and ownership |
| --- | --- |
| <https://github.com/rayborg/spacerocks-coa> | Confirmed project repository URL controlled by its owner |
| <https://rayborg.github.io/spacerocks-coa/> | Confirmed GitHub Pages URL; not evidence of a timestamp backend or paid production service |
| <https://www.lpi.usra.edu/meteor/> | Confirmed external Meteoritical Bulletin root owned by LPI/USRA |
| `https://www.lpi.usra.edu/meteor/metbull.cfm?code=<METBULL_CODE>` | User-verified canonical URL template, not a runtime API |
| `GET /v1/meteorites/metbull?code=<METBULL_CODE>` | Optional exact-code lookup from the bundled read-only LPI snapshot; no runtime upstream request |
| `VITE_TIMESTAMP_API_URL` | Public frontend API base; absent removes paid UI/requests; no production value is confirmed |
| `VITE_TIMESTAMP_SERVICE_MODE` | Public `sandbox` or `production` frontend mode |
| `VITE_TIMESTAMP_POLICY_VERSION` | Public policy version; production rejects Phase-0 values |
| `VITE_TIMESTAMP_TERMS_URL` | Required reviewed HTTPS URL in production; none confirmed |
| `VITE_TIMESTAMP_PRIVACY_URL` | Required reviewed HTTPS URL in production; none confirmed |
| `VITE_TIMESTAMP_REFUND_URL` | Required reviewed HTTPS URL in production; none confirmed |
| `VITE_TIMESTAMP_SUPPORT_EMAIL` | Required public support email in production; none confirmed |
| `GET /health/live` | Implemented relative API route; process response only |
| `GET /health/ready` | Implemented relative API route; configured database query only |
| `POST /v1/checkout` | Implemented Checkout creation route |
| `POST /v1/webhooks/stripe` | Implemented signed Stripe webhook route; no live registration confirmed |
| `POST /v1/webhooks/resend` | Implemented signed Resend webhook route; no provider registration confirmed |
| `GET /v1/orders/status` | Implemented bearer-authenticated status route |
| `GET /v1/orders/proof` | Implemented bearer-authenticated proof route |
| `POST /v1/orders/rotate-token` | Implemented bearer-authenticated token-rotation route |
| `POST /v1/orders/checkout` | Not a route; do not publish it |

## Runtime factories

- Timestamp worker: `TIMESTAMP_WORKER_FACTORY=app.worker.composition:create_worker`
- Notification worker: `NOTIFICATION_WORKER_FACTORY=app.notifications.worker:create_notification_worker`
- Operator task: `TIMESTAMP_OPERATOR_FACTORY=app.worker.operator:create_operator_commands`

Provider settings and gates are documented in `TIMESTAMP_SERVICE_DEPLOYMENT.md`. No provider-generated calendar, API, webhook, Checkout, support, DNS, Neon, Bitcoin RPC, or email URL may be invented or recorded as active until the owner-authorized operation verifies it.
