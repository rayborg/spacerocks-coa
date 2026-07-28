# Spacerocks COA Studio

A static, browser-only application for creating and verifying self-contained meteorite Certificates of Authenticity.

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

The application is hosted as static files on GitHub Pages. It has no application server and sends no form, image, passphrase, or key data to a backend.

## Development

```bash
npm install
npm run dev
```

Validation:

```bash
npm run typecheck
npm test
npm run build
```

## PDF/A limitation

The generated PDF is a standard archival PDF containing the high-resolution certificate image. It is not claimed to be PDF/A until independently converted and validated with veraPDF. The package includes PNG and plain-text archival alternatives.

## Deployment

Pushes to `main` deploy `dist/` through `.github/workflows/deploy-pages.yml`. GitHub Pages must use GitHub Actions as its source.
