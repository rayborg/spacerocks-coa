# Spacerocks COA Studio

A static, browser-only application for creating and verifying self-contained meteorite Certificates of Authenticity. The free local COA is the foundational product and requires no backend or paid service.

## Core properties

- Generates issuer-controlled Ed25519 signing identities in the browser.
- Encrypts private-key backups with PBKDF2-SHA-256 and AES-256-GCM.
- Hashes exact specimen photographs locally with SHA-256.
- Creates a deterministic, versioned JSON manifest.
- Exports certificate PDF, PNG, UTF-8 text, record, audit log, schema, signature, public key, checksums, instructions, and an offline Python verifier in one ZIP.
- Verifies generated and compatible legacy packages in the browser without uploading them.
- Requires no backend, database, domain, blockchain, IPFS, or Arweave for core verification.

## Security model

The private signing key is held only in browser memory and in the encrypted backup explicitly downloaded by the issuer. It is never included in a COA package. The public-key fingerprint must be published through independent trusted channels to associate the cryptographic identity with the human issuer.

Without optional service configuration, the application is hosted as static files on GitHub Pages. It has no application server and sends no form, image, passphrase, key, or certificate data to a backend.

## Optional managed timestamp service

Code for an optional paid Bitcoin OpenTimestamps service is present and code-complete for the current MVP scope. Production is not publicly live and real customer payments are unavailable. A Stripe-test sandbox was previously deployed and checkout/refund canaries were recorded as working, but that recovered result has not been freshly verified and is not launch evidence. The UI is absent and makes no timestamp-service requests unless an operator explicitly builds the frontend with a valid `VITE_TIMESTAMP_API_URL` and mode configuration.

After a signed COA is created locally and the customer explicitly consents, the optional checkout request sends only:

- the certificate reference;
- the lowercase SHA-256 of the exact final UTF-8 bytes of `manifest.json`;
- the fulfillment email address; and
- the managed-service consent record, including terms version, privacy version, and acceptance time.

It does not send the manifest, COA ZIP, certificate image, specimen photographs, private signing key, passphrase, physical address, provenance data, form record, or card data. Stripe-hosted Checkout would handle payment details. The digest is potentially pseudonymous, not necessarily anonymous. A browser return never proves payment, and a pending calendar proof is never Bitcoin confirmation.

The worker's direct-digest method converts the 64 lowercase hexadecimal characters back to the original 32 digest bytes and constructs a detached OpenTimestamps proof without hashing the hex text or digest again. The attached, untracked `/Users/rbj/Desktop/OpenTimestamps_COA_Methodology.md` is the detailed direct-digest methodology and is not modified by this repository. The in-repository [exact digest contract](docs/PAID_BITCOIN_TIMESTAMP_SERVICE_PLAN.md#8-exact-digest-contract) summarizes the same boundary.

References:

- [Phase 0 contracts](contracts/)
- Attached direct-digest methodology: `OpenTimestamps_COA_Methodology.md` on the project owner's desktop (outside this repository)
- [Paid service plan and gate status](docs/PAID_BITCOIN_TIMESTAMP_SERVICE_PLAN.md)
- [Backend local and test guide](timestamp-service/README.md)
- [Operations runbook](docs/TIMESTAMP_SERVICE_OPERATIONS.md)
- [Threat model](docs/TIMESTAMP_SERVICE_THREAT_MODEL.md)
- [Deployment preparation](docs/TIMESTAMP_SERVICE_DEPLOYMENT.md)

## Development

```bash
npm ci
npm run dev
```

Validation:

```bash
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Backend validation uses Python 3.12 and the pinned lock:

```bash
cd timestamp-service
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements.lock
python -m pip check
ruff check app migrations scripts tests
mypy
pytest
```

## PDF/A limitation

The generated PDF is a standard archival PDF containing the high-resolution certificate image. It is not claimed to be PDF/A until independently converted and validated with veraPDF. The package includes PNG and plain-text archival alternatives.

## Deployment

Pushes to `main` deploy `dist/` through `.github/workflows/deploy-pages.yml`. GitHub Pages must use GitHub Actions as its source.

## Service release boundary

The backend now composes explicitly gated Stripe test/live payments, hardened allowlisted public calendars, private Bitcoin Core RPC verification, durable Resend notification delivery, and a separate notification worker. Safe defaults remain `disabled`; fixture adapters remain pytest-only. `stripe_live` is accepted only with `APP_ENV=production`, `STRIPE_LIVE_ENABLED=true`, matching live Stripe credentials, non-Phase-0 product/policy versions, HTTPS origins, and all other validated settings. Code capability is not deployment authorization.

The service limits each raw `.ots` proof to 262,144 bytes. Proof versions and Bitcoin confirmation observations are append-only. The first verified observation at one or more confirmations creates the bundle and initial email; only a verified Resend `email.delivered` webhook can move `bitcoin_verified` to `delivered`. Confirmation monitoring then sends a final notice at six or more confirmations. Lost, decreased, or conflicting Bitcoin evidence fails closed to `manual_review`; historical artifacts do not authorize downloads from that state.

Neon is the current database target. On 2026-08-27, migration `20260827_0002` and all 270 backend tests passed against isolated branches cloned from production; a separate clean clone reached head with 16 tables, eight application triggers, and zero business/evidence rows. Production was empty, so customer-data restore, checksum, and proof-reverification behavior remains untested until real data exists. Private deployment checks, Resend domain/DNS and webhook setup, calendar pilot authorization, private Bitcoin Core RPC checks, Stripe tax/live webhook configuration, production DNS/TLS, live canaries, policies, monitoring, and explicit account-owner approval remain blocked. `timestamp-service/railway.toml` is optional deployment metadata, not evidence of Railway infrastructure or a Railway Postgres target.
