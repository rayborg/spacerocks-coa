import JSZip from "jszip";
import type { VerificationCheck, VerificationResult } from "../types";
import { publicKeyFingerprint, pemToDer } from "./crypto";
import { sha256Hex, utf8 } from "./core";
import { validateManifestVersion, validateOfficialMeteoriteIdentity } from "./manifest-validation";

const MAX_ZIP_BYTES = 300 * 1024 * 1024;
const MAX_FILE_COUNT = 250;
const MAX_ENTRY_BYTES = 200 * 1024 * 1024;
const MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024;
const STRUCTURAL_FILES = new Set(["manifest.json", "signature.sig", "sha256sums.txt"]);

interface RawZipEntry {
  name: string;
  compressedBytes: number;
  uncompressedBytes: number;
}

function inspectZipCentralDirectory(bytes: Uint8Array<ArrayBuffer>): RawZipEntry[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const minimumEocdOffset = Math.max(0, bytes.byteLength - 65_557);
  let eocdOffset = -1;
  for (let offset = bytes.byteLength - 22; offset >= minimumEocdOffset; offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50) {
      eocdOffset = offset;
      break;
    }
  }
  if (eocdOffset < 0) throw new Error("The file does not contain a valid ZIP end record.");

  const diskNumber = view.getUint16(eocdOffset + 4, true);
  const centralDisk = view.getUint16(eocdOffset + 6, true);
  const entriesOnDisk = view.getUint16(eocdOffset + 8, true);
  const entryCount = view.getUint16(eocdOffset + 10, true);
  const centralSize = view.getUint32(eocdOffset + 12, true);
  const centralOffset = view.getUint32(eocdOffset + 16, true);
  const commentLength = view.getUint16(eocdOffset + 20, true);
  if (eocdOffset + 22 + commentLength > bytes.byteLength) throw new Error("The ZIP end record is truncated.");
  if (diskNumber !== 0 || centralDisk !== 0 || entriesOnDisk !== entryCount) {
    throw new Error("Multi-disk ZIP packages are not supported.");
  }
  if (entryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) {
    throw new Error("ZIP64 packages are not supported by the browser verifier.");
  }
  if (centralOffset + centralSize > eocdOffset) throw new Error("The ZIP central directory is invalid.");

  const decoder = new TextDecoder("utf-8", { fatal: true });
  const entries: RawZipEntry[] = [];
  const names = new Set<string>();
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > eocdOffset || view.getUint32(offset, true) !== 0x02014b50) {
      throw new Error("The ZIP central directory contains an invalid entry.");
    }
    const compressedBytes = view.getUint32(offset + 20, true);
    const uncompressedBytes = view.getUint32(offset + 24, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const entryCommentLength = view.getUint16(offset + 32, true);
    const recordLength = 46 + nameLength + extraLength + entryCommentLength;
    if (offset + recordLength > eocdOffset) throw new Error("The ZIP central directory entry is truncated.");

    let name: string;
    try {
      name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLength));
    } catch {
      throw new Error("The ZIP contains a filename that is not valid UTF-8.");
    }
    if (names.has(name)) throw new Error(`The ZIP contains a duplicate entry: ${name}`);
    names.add(name);
    entries.push({ name, compressedBytes, uncompressedBytes });
    offset += recordLength;
  }
  if (offset !== centralOffset + centralSize) throw new Error("The ZIP central-directory size does not match its entries.");
  return entries;
}

function isSafeRelativePath(path: string): boolean {
  return Boolean(path) && !path.startsWith("/") && !path.includes("\\") && !path.split("/").includes("..");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown verification error";
}

export async function verifyCertificateZip(file: File): Promise<VerificationResult> {
  if (file.size > MAX_ZIP_BYTES) {
    throw new Error("The ZIP exceeds the 300 MB browser verification limit.");
  }

  const zipBytes = new Uint8Array(await file.arrayBuffer());
  const rawEntries = inspectZipCentralDirectory(zipBytes);
  const rawFiles = rawEntries.filter((entry) => !entry.name.endsWith("/"));
  if (rawFiles.length > MAX_FILE_COUNT) throw new Error("The ZIP contains too many files to verify safely in the browser.");
  let uncompressedBytes = 0;
  for (const entry of rawFiles) {
    if (entry.uncompressedBytes > MAX_ENTRY_BYTES) {
      throw new Error(`The ZIP entry ${entry.name} exceeds the 200 MB limit.`);
    }
    uncompressedBytes += entry.uncompressedBytes;
    if (uncompressedBytes > MAX_UNCOMPRESSED_BYTES) {
      throw new Error("The ZIP exceeds the 500 MB uncompressed verification limit.");
    }
  }

  const zip = await JSZip.loadAsync(zipBytes, { checkCRC32: false });
  const entries = rawFiles.map((entry) => entry.name);

  const manifestEntries = entries.filter((name) => name === "manifest.json" || name.endsWith("/manifest.json"));
  if (manifestEntries.length === 0) throw new Error("No manifest.json was found in the ZIP.");
  if (manifestEntries.length > 1) throw new Error("The ZIP contains multiple package manifests.");
  const manifestEntry = manifestEntries[0];
  const root = manifestEntry.slice(0, -"manifest.json".length);
  const entryFor = (relative: string) => {
    if (!isSafeRelativePath(relative)) return null;
    return zip.file(`${root}${relative}`);
  };
  const readBytes = async (relative: string) => {
    const entry = entryFor(relative);
    if (!entry) throw new Error(`Missing package file: ${relative}`);
    return entry.async("uint8array");
  };
  const readText = async (relative: string) => new TextDecoder().decode(await readBytes(relative));

  const checks: VerificationCheck[] = [];
  const manifestBytes = await readBytes("manifest.json");
  let manifest: Record<string, any>;
  try {
    manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
  } catch {
    throw new Error("manifest.json is not valid JSON.");
  }

  const manifestValidation = validateManifestVersion(manifest);
  if (manifestValidation.version === "v1" || manifestValidation.version === "v2") {
    checks.push({
      label: "Manifest schema",
      status: manifestValidation.valid ? "pass" : "fail",
      detail: manifestValidation.valid
        ? `The manifest conforms to coa-manifest-${manifestValidation.version}.`
        : (manifestValidation.errors ?? [])
            .slice(0, 3)
            .map((item) => `${item.instancePath || "/"} ${item.message}`)
            .join("; "),
    });
  } else if (manifestValidation.version === "mismatch") {
    checks.push({
      label: "Manifest schema",
      status: "fail",
      detail: "The manifest uses a mismatched known $schema, schemaVersion, or packageVersion identifier.",
    });
  } else {
    checks.push({
      label: "Manifest schema",
      status: "warning",
      detail: "This manifest version is unknown; cryptographic checks will still be performed.",
    });
  }

  if (manifestValidation.version === "v2") {
    const identityCheck = validateOfficialMeteoriteIdentity(manifest.specimen);
    checks.push({
      label: "Official meteorite identity",
      status: identityCheck.valid ? "pass" : "fail",
      detail: identityCheck.detail,
    });
  }

  const publicKeyPem = await readText("public-key.pem");
  const actualFingerprint = await publicKeyFingerprint(publicKeyPem);
  const expectedFingerprint = manifest.issuer?.publicKeyFingerprint ?? "";
  checks.push({
    label: "Public-key fingerprint",
    status: actualFingerprint === expectedFingerprint ? "pass" : "fail",
    detail:
      actualFingerprint === expectedFingerprint
        ? actualFingerprint
        : `Computed ${actualFingerprint}; manifest records ${expectedFingerprint || "none"}.`,
  });

  try {
    const publicKey = await crypto.subtle.importKey("spki", pemToDer(publicKeyPem), "Ed25519", false, ["verify"]);
    const signature = new Uint8Array(await readBytes("signature.sig"));
    const signatureValid = await crypto.subtle.verify(
      "Ed25519",
      publicKey,
      signature,
      new Uint8Array(manifestBytes),
    );
    checks.push({
      label: "Ed25519 signature",
      status: signatureValid ? "pass" : "fail",
      detail: signatureValid
        ? "The exact manifest bytes were signed by the bundled public key."
        : "The manifest signature is invalid.",
    });
  } catch (error) {
    checks.push({
      label: "Ed25519 signature",
      status: "fail",
      detail: errorMessage(error),
    });
  }

  const listedFiles = Array.isArray(manifest.files) ? manifest.files : [];
  const failedEvidence: string[] = [];
  let verifiedEvidence = 0;
  for (const entry of listedFiles) {
    if (!entry || typeof entry.path !== "string" || !isSafeRelativePath(entry.path)) {
      failedEvidence.push(String(entry?.path ?? "invalid path"));
      continue;
    }
    try {
      const bytes = await readBytes(entry.path);
      const hash = await sha256Hex(bytes);
      if (hash !== entry.sha256 || bytes.byteLength !== entry.bytes) failedEvidence.push(entry.path);
      else verifiedEvidence += 1;
    } catch {
      failedEvidence.push(entry.path);
    }
  }
  checks.push({
    label: "Signed evidence files",
    status: listedFiles.length > 0 && failedEvidence.length === 0 ? "pass" : "fail",
    detail:
      failedEvidence.length === 0
        ? `${verifiedEvidence} file${verifiedEvidence === 1 ? "" : "s"} match the signed byte lengths and SHA-256 hashes.`
        : `Failed: ${failedEvidence.slice(0, 5).join(", ")}${failedEvidence.length > 5 ? "..." : ""}`,
  });

  if (manifestValidation.version === "v1" || manifestValidation.version === "v2") {
    const expectedFiles = new Set<string>([
      ...listedFiles.map((entry: { path?: unknown }) => String(entry?.path ?? "")),
      ...STRUCTURAL_FILES,
    ]);
    const outsideRoot = root ? entries.filter((name) => !name.startsWith(root)) : [];
    const relativeEntries = entries
      .filter((name) => !root || name.startsWith(root))
      .map((name) => name.slice(root.length));
    const duplicateEntries = relativeEntries.filter((name, index) => relativeEntries.indexOf(name) !== index);
    const unexpected = relativeEntries.filter((name) => !expectedFiles.has(name));
    const missing = Array.from(expectedFiles).filter((name) => !relativeEntries.includes(name));
    const inventoryValid =
      outsideRoot.length === 0 &&
      duplicateEntries.length === 0 &&
      unexpected.length === 0 &&
      missing.length === 0 &&
      relativeEntries.length === expectedFiles.size;
    checks.push({
      label: "Package inventory",
      status: inventoryValid ? "pass" : "fail",
      detail:
        inventoryValid
          ? `${relativeEntries.length} package files are either signed or required structural files.`
          : `Outside root: ${outsideRoot.join(", ") || "none"}; duplicates: ${duplicateEntries.join(", ") || "none"}; unexpected: ${unexpected.join(", ") || "none"}; missing: ${missing.join(", ") || "none"}.`,
    });
  } else {
    checks.push({
      label: "Package inventory",
      status: "warning",
      detail: "Strict signed inventory enforcement is unavailable for this legacy package.",
    });
  }

  const recordFile = manifest.certificate?.recordFile;
  const expectedRecordHash = manifest.certificate?.recordSha256;
  if (typeof recordFile === "string" && typeof expectedRecordHash === "string") {
    try {
      const actualRecordHash = await sha256Hex(await readBytes(recordFile));
      checks.push({
        label: "Certificate record",
        status: actualRecordHash === expectedRecordHash ? "pass" : "fail",
        detail:
          actualRecordHash === expectedRecordHash
            ? actualRecordHash
            : `Computed ${actualRecordHash}; manifest records ${expectedRecordHash}.`,
      });
    } catch (error) {
      checks.push({ label: "Certificate record", status: "fail", detail: errorMessage(error) });
    }
  } else {
    checks.push({
      label: "Certificate record",
      status: "warning",
      detail: "This older package does not contain a separate immutable certificate record.",
    });
  }

  try {
    const checksumText = await readText("sha256sums.txt");
    const lines = checksumText.split(/\r?\n/).filter(Boolean);
    const failedChecksums: string[] = [];
    let matched = 0;
    for (const line of lines) {
      const match = line.match(/^([a-fA-F0-9]{64}) {2}(.+)$/);
      if (!match || !isSafeRelativePath(match[2])) {
        failedChecksums.push(match?.[2] ?? "invalid checksum line");
        continue;
      }
      try {
        const actual = await sha256Hex(await readBytes(match[2]));
        if (actual !== match[1].toLowerCase()) failedChecksums.push(match[2]);
        else matched += 1;
      } catch {
        failedChecksums.push(match[2]);
      }
    }
    checks.push({
      label: "Package checksums",
      status: lines.length > 0 && failedChecksums.length === 0 ? "pass" : "fail",
      detail:
        failedChecksums.length === 0
          ? `${matched} distributed files match sha256sums.txt.`
          : `Failed: ${failedChecksums.slice(0, 5).join(", ")}`,
    });
  } catch (error) {
    checks.push({ label: "Package checksums", status: "fail", detail: errorMessage(error) });
  }

  return {
    valid: checks.every((check) => check.status !== "fail"),
    certificateId: manifest.certificate?.id ?? manifest.certificateId ?? "Unknown certificate",
    fingerprint: actualFingerprint,
    checks,
  };
}

export async function verifyManifestSignatureForTest(
  manifest: string,
  signature: Uint8Array<ArrayBufferLike>,
  publicKeyPem: string,
): Promise<boolean> {
  const publicKey = await crypto.subtle.importKey("spki", pemToDer(publicKeyPem), "Ed25519", false, ["verify"]);
  return crypto.subtle.verify("Ed25519", publicKey, new Uint8Array(signature), utf8(manifest));
}
