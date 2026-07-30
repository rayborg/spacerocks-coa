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

Phase 0 code for an optional managed Bitcoin OpenTimestamps service is present on the `feature/on-chain-anchoring` branch. It is sandbox-only and is not a live service. The UI is absent and makes no timestamp-service requests unless an operator explicitly builds the frontend with a valid `VITE_TIMESTAMP_API_URL`.

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

Phase 0 is deterministic local/sandbox implementation only. Live payment mode is rejected, and fixture payment/calendar/Bitcoin adapters run only inside `pytest`. Public calendar transport is intentionally unavailable and cannot be selected through settings or runtime composition until a pinned-public-IP TLS/SNI transport is implemented and independently reviewed. Production Bitcoin verification and email sending are absent; without a sender, fulfillment remains `bitcoin_verified` and never becomes `delivered`.

The service limits each raw `.ots` proof to 262,144 bytes. Proof versions are append-only, the latest valid version defines current proof state, and manual review suppresses proof availability until an audited recovery transition exists. Bitcoin verification and downloadable-artifact readiness are separate: `bitcoin_verified` can briefly report no available proof until the matching bundle is persisted by a later durable job, and no sender means the state remains `bitcoin_verified`. Ordinary pending confirmation uses durable six-hour successor polling rather than a short retry/dead-letter window. Price, customer policies, polling and retention bounds, backup/restore testing, monitoring, reorganization-aware verification/reverification, provider accounts, and all other live gates in the plan remain external blockers.
