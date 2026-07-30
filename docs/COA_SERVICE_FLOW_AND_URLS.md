# COA Service Flow and URLs

**Status:** The core COA is local and browser-only. The optional managed timestamp service is Phase 0 sandbox code only, is not deployed, and fails closed when it is not explicitly configured.

**Last reviewed:** 2026-07-30

## Local COA flow

1. Create or import the issuer-controlled Ed25519 signing identity in the browser. The private key and its passphrase must remain local.
2. Choose the specimen path:
   - **Official meteorite:** open the official [Meteoritical Bulletin](https://www.lpi.usra.edu/meteor/), find the canonical record, and personally confirm and attest to the exact canonical name, type, class, subclass, Meteoritical Bulletin code, and record URL.
   - **Unknown or unlisted specimen:** use **Unclassified**. A suspected type may be recorded, but it is optional, non-authoritative, and must not be presented as an official classification.
3. Complete the shared specimen and provenance fields. Finder and intermediary purchaser are optional. Location requires country plus at least one of locality/city, region, or a complete latitude-and-longitude pair; one coordinate without the other is invalid.
4. Add exact specimen photographs and confirm the photo safeguards. At least one unmodified original is required, and specimen photographs must not be generated or altered with AI.
5. Preview and review the certificate. Issuance remains blocked until every required and conditionally required field is valid, an appropriate signing identity is active, and all signing, private-key, and photograph safeguards pass.
6. Generate the deterministic manifest and certificate files, hash the evidence files, sign the exact final manifest bytes, and build the self-contained ZIP locally.
7. Keep the encrypted private-key backup separate. The release contains the public key and signature, never the private key or passphrase.
8. Verify locally by importing the COA package into the browser verifier or by running the packaged offline verifier. Verification checks the Ed25519 signature, public-key fingerprint, manifest binding, and file hashes without a website or service.

There is no official catalog API and no runtime catalog lookup. The application must not silently infer or fetch official meteorite data. The user opens LPI, verifies the canonical values and URL, enters them, and attests to that verification. This user attestation does not make the application an independent catalog authority.

## Optional managed timestamp flow

This supplemental flow starts only after the local signed COA has been released and the user explicitly opts in:

1. Local COA release -> browser computes the SHA-256 of the exact final `manifest.json` bytes.
2. Explicit consent -> frontend sends only the certificate reference, manifest digest, delivery email, and managed-service consent record to the configured timestamp API.
3. API order -> Stripe-hosted sandbox checkout -> signed Stripe webhook confirms payment; a browser redirect is never payment authority.
4. Durable worker -> exact 32-byte digest is submitted to OpenTimestamps calendars -> pending proof is retained -> later upgrades are checked for an exact-digest Bitcoin attestation.
5. Authenticated status/proof routes -> customer downloads the separate timestamp proof bundle when its state and durable artifact permit it.

The service is sandbox-only and fail-closed. Live payments are rejected; public-calendar runtime transport, production Bitcoin verification, email sending, and live deployment are not enabled. A pending calendar proof is not Bitcoin confirmation, and the supplemental proof does not replace or validate the underlying local COA.

## Data boundary

The COA form data, specimen photographs, complete COA ZIP, private signing key, key passphrase, and encrypted key backup remain local. They are not uploaded to the optional service.

After local release and explicit opt-in, the optional service receives only:

- certificate reference;
- lowercase SHA-256 manifest digest;
- delivery email; and
- consent record, including policy versions and acceptance time.

The digest can be pseudonymous and is not guaranteed anonymous. Payment details belong on Stripe-hosted Checkout if a future sandbox exercise is authorized.

## URL and route register

| URL, template, or route | Category | Status and ownership |
| --- | --- | --- |
| <https://github.com/rayborg/spacerocks-coa> | Confirmed URL | Project GitHub repository. Repository owner controls it. |
| <https://rayborg.github.io/spacerocks-coa/> | Confirmed URL | Current public GitHub Pages site deployed from `main`; it does not necessarily contain `feature/on-chain-anchoring`. It is not evidence of a live timestamp backend. |
| <https://www.lpi.usra.edu/meteor/> | Confirmed external URL | Official Meteoritical Bulletin root, owned by LPI/USRA rather than this project. Users perform canonical record verification here. |
| `https://www.lpi.usra.edu/meteor/metbull.cfm?code=<METBULL_CODE>` | Canonical URL template | User substitutes the exact Meteoritical Bulletin code and confirms the resulting record. This is a template, not a runtime lookup endpoint. |
| `VITE_TIMESTAMP_API_URL` | Environment-configured API base | Pending. Public frontend build variable, not a secret and not itself a fixed URL. The paid UI is absent when unset. Only a reviewed HTTPS base URL is allowed outside local development/test. |
| `GET /health/live` | Source-confirmed relative backend route | Implemented relative route; no production API origin is resolved. It proves only that the API process responds. |
| `GET /health/ready` | Source-confirmed relative backend route | Implemented relative route; no production API origin is resolved. It checks the configured PostgreSQL store, not providers or workers. |
| `POST /v1/orders/checkout` | Unimplemented candidate route | Not present in the backend contract. Do not publish it as the checkout URL. Source defines `POST /v1/checkout`. |
| `POST /v1/checkout` | Source-confirmed relative backend route | Implemented checkout route used by the frontend; deployment and provider-backed operation remain pending. |
| `POST /v1/webhooks/stripe` | Source-confirmed relative backend route | Implemented signed-webhook route; no registered provider endpoint or production origin is confirmed. |
| `GET /v1/orders/status` | Source-confirmed relative backend route | Implemented bearer-authenticated route; no production origin is confirmed. |
| `GET /v1/orders/proof` | Source-confirmed relative backend route | Implemented bearer-authenticated proof route; no production origin is confirmed. |
| `POST /v1/orders/rotate-token` | Source-confirmed relative backend route | Implemented bearer-authenticated token-rotation route; no production origin is confirmed. |

## Unresolved provider URLs

No production timestamp API, Railway service, Stripe Checkout Session, Stripe webhook registration, public-calendar runtime endpoint, Bitcoin verifier, transactional email provider, support calendar, or email URL is confirmed. These values must not be invented. Provider-generated URLs may be recorded only after the relevant sandbox gate, owner decision, security review, and explicit authorization are complete.

## Source evidence

Route spellings are defined in `timestamp-service/app/api/routes.py`; frontend route construction and `VITE_TIMESTAMP_API_URL` handling are defined in `src/lib/timestamp-service.ts`. Operational limitations and data minimization are detailed in `PAID_BITCOIN_TIMESTAMP_SERVICE_PLAN.md`, `TIMESTAMP_SERVICE_DEPLOYMENT.md`, and `TIMESTAMP_SERVICE_OPERATIONS.md`.
