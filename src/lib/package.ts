import JSZip from "jszip";
import manifestSchema from "../../schemas/coa-manifest-v2.schema.json";
import type { FormValues, ManifestFile, PhotoInput, SigningIdentity } from "../types";
import {
  displayDate,
  mediaTypeForFile,
  sanitizeFileName,
  sha256Hex,
  stableStringify,
  uniqueFileName,
  utf8,
} from "./core";
import { signBytes } from "./crypto";
import { renderCertificate } from "./certificate";
import { getCertificateTheme } from "../certificateThemes";
import { getCertificateStyle } from "../certificateStyles";
import { isValidIsoDate, validateFormValues } from "./form-validation";
import { assertV2Manifest } from "./manifest-validation";

const APPLICATION_VERSION = "1.0.0";
const MAX_PHOTO_COUNT = 100;
const MAX_PHOTO_BYTES = 100 * 1024 * 1024;
const MAX_SOURCE_BYTES = 200 * 1024 * 1024;

interface PackageInput {
  values: FormValues;
  photos: PhotoInput[];
  logo?: File;
  identity: SigningIdentity;
}

interface PreparedPhoto {
  path: string;
  originalFilename: string;
  caption?: string;
  mediaType: string;
  bytes: number;
  sha256: string;
  captureDate?: string;
  isUnmodifiedOriginal: true;
  content: Uint8Array;
}

interface PackageResult {
  blob: Blob;
  fileName: string;
  recordHash: string;
  manifestHash: string;
}

function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function recorded(value: string): string {
  return optional(value) ?? "Not recorded";
}

function recordedCoordinates(latitude: string, longitude: string): string {
  return [optional(latitude), optional(longitude)].filter(Boolean).join(", ") || "Not recorded";
}

function buildSpecimen(values: FormValues) {
  const official = values.meteoriteIdentity === "official";
  return {
    meteorite: values.meteoriteName.trim(),
    meteoriteIdentity: values.meteoriteIdentity,
    meteoriteType: official ? values.meteoriteType.trim() : "Unclassified",
    classification: official ? values.classification.trim() : "Unclassified",
    meteoriteSubclass: official ? values.meteoriteSubclass.trim() : "Unclassified",
    suspectedType: official ? undefined : optional(values.suspectedType),
    officialNameVerified: official ? true : undefined,
    weightGrams: Number(values.weightGrams),
    weightPrecision: Number(values.weightPrecision),
    form: values.specimenForm.trim(),
    dimensions: optional(values.dimensions),
    numberOfPieces: Number(values.numberOfPieces),
    preparationState: optional(values.preparationState),
    identifyingMarks: optional(values.identifyingMarks),
    recordedOwner: optional(values.recordedOwner),
    fall: {
      status: values.fallStatus.trim(),
      date: optional(values.fallDate),
      country: values.country.trim(),
      region: optional(values.region),
      locality: optional(values.locality),
      latitude: optional(values.latitude),
      longitude: optional(values.longitude),
      metbullCode: official ? values.metbullCode.trim() : undefined,
      officialReferenceUrl: official ? values.officialReferenceUrl.trim() : undefined,
      finderName: optional(values.finderName),
      recoveryInformation: optional(values.recoveryInformation),
    },
    provenance: {
      statement: optional(values.provenance),
      previousOwner: optional(values.previousOwner),
      intermediaryPurchaserName: optional(values.intermediaryPurchaserName),
      buyer: optional(values.buyer),
      transferDate: optional(values.transferDate),
      invoiceReference: optional(values.invoiceReference),
      transferNotes: optional(values.transferNotes),
    },
  };
}

function buildIssuer(values: FormValues, identity: SigningIdentity, logoPath?: string) {
  return {
    name: values.issuerName.trim(),
    collection: values.collectionName.trim(),
    email: optional(values.issuerEmail),
    phone: optional(values.issuerPhone),
    address: optional(values.issuerAddress),
    website: optional(values.issuerWebsite),
    logoFile: logoPath,
    publicKeyAlgorithm: "Ed25519",
    publicKeyFile: "public-key.pem",
    publicKeyFingerprint: identity.fingerprint,
    publicKeyFingerprintMethod: "SHA-256 of SubjectPublicKeyInfo DER",
  };
}

function buildCertificateText(
  values: FormValues,
  identity: SigningIdentity,
  recordHash: string,
  photoCount: number,
) {
  const official = values.meteoriteIdentity === "official";
  const meteoriteType = official ? values.meteoriteType.trim() : "Unclassified";
  const classification = official ? values.classification.trim() : "Unclassified";
  const meteoriteSubclass = official ? values.meteoriteSubclass.trim() : "Unclassified";
  return `${values.collectionName.toUpperCase()}
CERTIFICATE OF AUTHENTICITY - PLAIN-TEXT ARCHIVAL DUPLICATE

Certificate ID: ${values.certificateId}
Certificate version: ${values.certificateVersion}
Certificate status: ${values.certificateStatus}
Certificate layout style: ${getCertificateStyle(values.certificateStyle).name}
Certificate color scheme: ${getCertificateTheme(values.certificateTheme).name}
Issue date: ${displayDate(values.issueDate)}

SPECIMEN
Meteorite: ${values.meteoriteName}
Identity mode: ${official ? "Official" : "Unclassified"}
Meteorite type: ${meteoriteType}
Meteorite class: ${classification}
Meteorite subclass: ${meteoriteSubclass}
${official ? "Official name verified: Yes - issuer attestation" : `Suspected type: ${recorded(values.suspectedType)}`}
Recorded weight: ${values.weightGrams} g
Weight precision: ${values.weightPrecision} g
Specimen form: ${values.specimenForm}
Dimensions: ${recorded(values.dimensions)}
Number of pieces: ${values.numberOfPieces}
Preparation state: ${recorded(values.preparationState)}
Identifying marks: ${recorded(values.identifyingMarks)}
Recorded owner: ${recorded(values.recordedOwner)}

FALL OR FIND
Status: ${values.fallStatus}
Date: ${displayDate(values.fallDate)}
Country: ${values.country}
Region: ${recorded(values.region)}
Locality / city: ${recorded(values.locality)}
Coordinates: ${recordedCoordinates(values.latitude, values.longitude)}
${official ? `Meteoritical Bulletin code: ${values.metbullCode.trim()}\nOfficial reference: ${values.officialReferenceUrl.trim()}` : ""}
Finder name: ${recorded(values.finderName)}
Recovery information: ${recorded(values.recoveryInformation)}

PROVENANCE
${recorded(values.provenance)}
Previous owner: ${recorded(values.previousOwner)}
Intermediary purchaser: ${recorded(values.intermediaryPurchaserName)}
Buyer / transferee: ${recorded(values.buyer)}

DIGITAL ATTESTATION
Issuer: ${values.issuerName}
Collection: ${values.collectionName}
Signature algorithm: Ed25519
Signed record: manifest.json
Signature file: signature.sig
Public key file: public-key.pem
Public-key fingerprint (SHA-256 of SubjectPublicKeyInfo DER):
${identity.fingerprint}
Certificate record SHA-256:
${recordHash}
Exact original specimen photographs included: ${photoCount}

The private key is not included. Verify the signature and every file hash using
verify.py or another Ed25519 and SHA-256 implementation. The package remains
verifiable without this website or any network connection.
`;
}

function buildAuditLog(values: FormValues, generatedAt: string, recordHash: string) {
  return stableStringify({
    format: "Spacerocks COA append-only audit log",
    version: 1,
    certificateId: values.certificateId,
    events: [
      {
        at: generatedAt,
        event: "issued",
        status: values.certificateStatus,
        version: values.certificateVersion,
        recordSha256: recordHash,
        actor: values.issuerName,
        note: optional(values.certificateNotes),
      },
    ],
  });
}

function buildReadme(values: FormValues, identity: SigningIdentity, recordHash: string) {
  return `SPACEROCKS SELF-CONTAINED CERTIFICATE OF AUTHENTICITY

Certificate ID: ${values.certificateId}
Meteorite: ${values.meteoriteName}
Recorded weight: ${values.weightGrams} g
Specimen form: ${values.specimenForm}
Issuer: ${values.issuerName}
Collection: ${values.collectionName}

START HERE

1. Keep this entire folder together.
2. Run: python3 verify.py
3. If needed, install the open-source dependency with:
   python3 -m pip install cryptography
4. Compare the public-key fingerprint with an independently published copy
   associated with the issuer.

Public-key fingerprint:
${identity.fingerprint}

Certificate record SHA-256:
${recordHash}

The digital signature authenticates the exact manifest bytes. The signed
manifest authenticates the certificate files and original specimen photographs.
sha256sums.txt additionally checks the distributed support files. No private key
is present. Verification does not require a website, blockchain, IPFS, Arweave,
or any other network service.

IMPORTANT LIMITATION

The browser-generated PDF is a standard archival PDF containing the certificate
image. It is not claimed to be PDF/A until independently converted and validated
with veraPDF. Preserve certificate.png and certificate.txt as open fallbacks.
`;
}

const VERIFICATION_INSTRUCTIONS = `OFFLINE VERIFICATION INSTRUCTIONS

Recommended cross-platform verification:

  python3 verify.py

Dependency if it is not already installed:

  python3 -m pip install cryptography

Direct OpenSSL 3.x signature verification:

  openssl pkeyutl -verify -pubin -inkey public-key.pem -rawin \\
    -in manifest.json -sigfile signature.sig

Expected OpenSSL output:

  Signature Verified Successfully

The verifier performs four checks:

1. The Ed25519 signature matches the exact manifest.json bytes.
2. The SHA-256 fingerprint of public-key.pem matches the signed manifest.
3. Every evidence file listed in manifest.json has the expected byte length and
   SHA-256 hash.
4. Every distributed support file listed in sha256sums.txt is unchanged.

The signing public key must also be associated with the named issuer through an
independent trusted source. Cryptography proves key possession and integrity; it
does not, by itself, prove a human identity.
`;

const VERIFY_PY = `#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    print("Missing dependency: cryptography")
    print("Install with: python3 -m pip install cryptography")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent
failures = []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(relative: str) -> Path:
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        raise ValueError(f"Unsafe package path: {relative}")
    return candidate


manifest_bytes = (ROOT / "manifest.json").read_bytes()
manifest = json.loads(manifest_bytes)
public_key = serialization.load_pem_public_key((ROOT / "public-key.pem").read_bytes())
if not isinstance(public_key, Ed25519PublicKey):
    print("FAIL  public-key.pem is not an Ed25519 key")
    sys.exit(1)

try:
    public_key.verify((ROOT / "signature.sig").read_bytes(), manifest_bytes)
    print("OK    Ed25519 signature on manifest.json")
except InvalidSignature:
    print("FAIL  Ed25519 signature on manifest.json")
    failures.append("signature")

public_der = public_key.public_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
fingerprint_hex = hashlib.sha256(public_der).hexdigest().upper()
fingerprint = ":".join(fingerprint_hex[index:index + 2] for index in range(0, 64, 2))
if fingerprint == manifest["issuer"]["publicKeyFingerprint"]:
    print("OK    public-key fingerprint")
else:
    print("FAIL  public-key fingerprint")
    failures.append("fingerprint")

for entry in manifest.get("files", []):
    relative = entry["path"]
    try:
        path = safe_path(relative)
        actual_hash = sha256_file(path)
        actual_bytes = path.stat().st_size
        ok = actual_hash == entry["sha256"] and actual_bytes == entry["bytes"]
    except (OSError, ValueError):
        ok = False
    print(f"{'OK   ' if ok else 'FAIL '} evidence: {relative}")
    if not ok:
        failures.append(relative)

record_file = manifest["certificate"]["recordFile"]
record_hash = sha256_file(safe_path(record_file))
if record_hash == manifest["certificate"]["recordSha256"]:
    print("OK    certificate record hash")
else:
    print("FAIL  certificate record hash")
    failures.append("certificate-record")

for line in (ROOT / "sha256sums.txt").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    expected, relative = line.split("  ", 1)
    try:
        ok = sha256_file(safe_path(relative)) == expected.lower()
    except (OSError, ValueError):
        ok = False
    print(f"{'OK   ' if ok else 'FAIL '} package: {relative}")
    if not ok:
        failures.append(f"package:{relative}")

expected_files = {entry["path"] for entry in manifest.get("files", [])}
expected_files.update({"manifest.json", "signature.sig", "sha256sums.txt"})
actual_files = {
    path.relative_to(ROOT).as_posix()
    for path in ROOT.rglob("*")
    if path.is_file()
}
if actual_files == expected_files:
    print("OK    package inventory")
else:
    unexpected = sorted(actual_files - expected_files)
    missing = sorted(expected_files - actual_files)
    print(f"FAIL  package inventory; unexpected={unexpected}; missing={missing}")
    failures.append("package-inventory")

print("\\nPublic-key fingerprint:")
print(fingerprint)
if failures:
    print("\\nPACKAGE VERIFICATION FAILED")
    sys.exit(1)
print("\\nPACKAGE VERIFICATION PASSED")
`;

async function addEvidenceFile(
  files: Map<string, Uint8Array>,
  manifestFiles: ManifestFile[],
  path: string,
  role: string,
  mediaType: string,
  content: Uint8Array,
) {
  files.set(path, content);
  manifestFiles.push({
    path,
    role,
    mediaType,
    bytes: content.byteLength,
    sha256: await sha256Hex(content),
  });
}

export async function buildCertificatePackage(input: PackageInput): Promise<PackageResult> {
  const validatedValues = validateFormValues(input.values);
  const values: FormValues = validatedValues.meteoriteIdentity === "unclassified"
    ? {
        ...validatedValues,
        meteoriteType: "Unclassified",
        classification: "Unclassified",
        meteoriteSubclass: "Unclassified",
        officialNameVerified: false,
        metbullCode: "",
        officialReferenceUrl: "",
      }
    : validatedValues;
  const { photos, logo, identity } = input;
  if (photos.length === 0) throw new Error("Add at least one exact specimen photograph.");
  if (photos.length > MAX_PHOTO_COUNT) {
    throw new Error(`A package can contain at most ${MAX_PHOTO_COUNT} original photographs.`);
  }
  for (const photo of photos) {
    if (!photo.file.type.startsWith("image/")) {
      throw new Error(`Specimen photograph ${photo.file.name} must have an image MIME type.`);
    }
    if (photo.file.size > MAX_PHOTO_BYTES) {
      throw new Error(`Specimen photograph ${photo.file.name} exceeds the 100 MB per-photo limit.`);
    }
    const captureDate = photo.captureDate.trim();
    if (captureDate && !isValidIsoDate(captureDate)) {
      throw new Error(`Specimen photograph ${photo.file.name} has an invalid capture date; use YYYY-MM-DD.`);
    }
  }
  const sourceBytes = photos.reduce((total, photo) => total + photo.file.size, logo?.size ?? 0);
  if (sourceBytes > MAX_SOURCE_BYTES) {
    throw new Error("Original photographs and issuer assets cannot exceed 200 MB in one browser package.");
  }
  if (photos.some((photo) => !photo.isUnmodifiedOriginal)) {
    throw new Error("Confirm that every specimen photograph is an unmodified original.");
  }

  const generatedAt = new Date().toISOString();
  const usedNames = new Set<string>();
  const preparedPhotos: PreparedPhoto[] = [];

  for (const photo of photos) {
    const fileName = uniqueFileName(photo.file.name, usedNames);
    const path = `original-photographs/${fileName}`;
    const content = new Uint8Array(await photo.file.arrayBuffer());
    preparedPhotos.push({
      path,
      originalFilename: photo.file.name,
      caption: optional(photo.caption),
      mediaType: mediaTypeForFile(photo.file),
      bytes: content.byteLength,
      sha256: await sha256Hex(content),
      captureDate: optional(photo.captureDate),
      isUnmodifiedOriginal: true,
      content,
    });
  }

  let logoPath: string | undefined;
  let logoContent: Uint8Array | undefined;
  if (logo) {
    logoPath = `issuer-assets/${sanitizeFileName(logo.name)}`;
    logoContent = new Uint8Array(await logo.arrayBuffer());
  }

  const issuer = buildIssuer(values, identity, logoPath);
  const specimen = buildSpecimen(values);
  const photographRecords = preparedPhotos.map(({ content: _content, ...record }) => record);
  const certificateRecord = {
    format: "Spacerocks immutable certificate record",
    version: 1,
    certificate: {
      id: values.certificateId.trim(),
      issueDate: values.issueDate,
      version: values.certificateVersion.trim(),
      status: values.certificateStatus,
      visualStyle: values.certificateStyle,
      visualTheme: values.certificateTheme,
      supersedes: optional(values.supersededCertificateId),
      notes: optional(values.certificateNotes),
    },
    issuer,
    specimen,
    photographs: photographRecords,
  };
  const recordText = stableStringify(certificateRecord);
  const recordBytes = utf8(recordText);
  const recordHash = await sha256Hex(recordBytes);
  const qrPayload = [
    "SPACEROCKS-COA-V2",
    `ID:${values.certificateId.trim()}`,
    `RECORD-SHA256:${recordHash}`,
    `KEY-FP:${identity.fingerprint}`,
  ].join("\n");

  const rendered = await renderCertificate({
    values,
    fingerprint: identity.fingerprint,
    recordHash,
    qrPayload,
    mainPhoto: photos[0].file,
    logo,
  });
  const certificateText = utf8(buildCertificateText(values, identity, recordHash, photos.length));
  const auditLog = utf8(buildAuditLog(values, generatedAt, recordHash));

  const packageFiles = new Map<string, Uint8Array>();
  const manifestFiles: ManifestFile[] = [];
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "certificate-record.json",
    "immutable certificate record",
    "application/json",
    recordBytes,
  );
  await addEvidenceFile(packageFiles, manifestFiles, "certificate.pdf", "visual certificate", "application/pdf", rendered.pdf);
  await addEvidenceFile(packageFiles, manifestFiles, "certificate.png", "visual certificate", "image/png", rendered.png);
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "certificate.txt",
    "plain-text archival duplicate",
    "text/plain; charset=utf-8",
    certificateText,
  );
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "audit-log.json",
    "append-only issuance event",
    "application/json",
    auditLog,
  );

  for (const photo of preparedPhotos) {
    await addEvidenceFile(
      packageFiles,
      manifestFiles,
      photo.path,
      "exact original specimen photograph",
      photo.mediaType,
      photo.content,
    );
  }
  if (logoPath && logoContent && logo) {
    await addEvidenceFile(packageFiles, manifestFiles, logoPath, "issuer logo", mediaTypeForFile(logo), logoContent);
  }

  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "public-key.pem",
    "issuer public key",
    "application/x-pem-file",
    utf8(identity.publicKeyPem),
  );
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "coa-manifest-v2.schema.json",
    "manifest JSON Schema",
    "application/schema+json",
    utf8(stableStringify(manifestSchema)),
  );
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "README-FIRST.txt",
    "package entry instructions",
    "text/plain; charset=utf-8",
    utf8(buildReadme(values, identity, recordHash)),
  );
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "verification-instructions.txt",
    "offline verification instructions",
    "text/plain; charset=utf-8",
    utf8(VERIFICATION_INSTRUCTIONS),
  );
  await addEvidenceFile(
    packageFiles,
    manifestFiles,
    "verify.py",
    "offline Python verifier",
    "text/x-python; charset=utf-8",
    utf8(VERIFY_PY),
  );

  manifestFiles.sort((left, right) => left.path.localeCompare(right.path));
  const manifest = {
    $schema: "coa-manifest-v2.schema.json",
    schemaVersion: "2.0.0",
    packageFormat: "Spacerocks Self-Contained COA Package",
    packageVersion: 2,
    recordType: "meteorite-certificate-of-authenticity",
    certificate: {
      id: values.certificateId.trim(),
      issueDate: values.issueDate,
      version: values.certificateVersion.trim(),
      status: values.certificateStatus,
      visualStyle: values.certificateStyle,
      visualTheme: values.certificateTheme,
      recordFile: "certificate-record.json",
      recordSha256: recordHash,
      supersedes: optional(values.supersededCertificateId),
      notes: optional(values.certificateNotes),
    },
    issuer,
    specimen,
    photographs: photographRecords,
    files: manifestFiles,
    signature: {
      algorithm: "Ed25519",
      signedFile: "manifest.json",
      signatureFile: "signature.sig",
      signatureEncoding: "raw 64-byte Ed25519 signature",
    },
    generation: {
      application: "Spacerocks COA Studio",
      version: APPLICATION_VERSION,
      generatedAt,
      manifestSerialization: "UTF-8, sorted keys, two-space indentation, LF, final newline, no BOM",
    },
  };
  assertV2Manifest(manifest as Record<string, unknown>);
  const manifestBytes = utf8(stableStringify(manifest));
  const signature = await signBytes(identity.privateKey, manifestBytes);
  const manifestHash = await sha256Hex(manifestBytes);

  packageFiles.set("manifest.json", manifestBytes);
  packageFiles.set("signature.sig", signature);

  const checksumLines: string[] = [];
  for (const path of Array.from(packageFiles.keys()).sort()) {
    checksumLines.push(`${await sha256Hex(packageFiles.get(path)!)}  ${path}`);
  }
  packageFiles.set("sha256sums.txt", utf8(`${checksumLines.join("\n")}\n`));

  const safeId = sanitizeFileName(values.certificateId) || "certificate";
  const zip = new JSZip();
  const zipDate = new Date(generatedAt);
  for (const [path, content] of packageFiles) {
    zip.file(`${safeId}/${path}`, content, {
      binary: true,
      date: zipDate,
      createFolders: true,
    });
  }
  const blob = await zip.generateAsync({
    type: "blob",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
    platform: "UNIX",
  });

  return {
    blob,
    fileName: `${safeId}-self-contained-coa.zip`,
    recordHash,
    manifestHash,
  };
}
