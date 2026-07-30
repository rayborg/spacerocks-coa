import { describe, expect, it } from "vitest";
import {
  assertV2Manifest,
  validateManifestVersion,
  validateOfficialMeteoriteIdentity,
} from "./manifest-validation";

const sha256 = "a".repeat(64);
const fingerprint = `${"AA:".repeat(31)}AA`;

function commonManifest() {
  return {
    packageFormat: "Spacerocks Self-Contained COA Package",
    recordType: "meteorite-certificate-of-authenticity",
    certificate: {
      id: "TEST-001",
      issueDate: "2026-07-30",
      version: "1.0",
      status: "active",
      recordFile: "certificate-record.json",
      recordSha256: sha256,
    },
    issuer: {
      name: "Test Issuer",
      collection: "Test Collection",
      publicKeyAlgorithm: "Ed25519",
      publicKeyFile: "public-key.pem",
      publicKeyFingerprint: fingerprint,
      publicKeyFingerprintMethod: "SHA-256 of SubjectPublicKeyInfo DER",
    },
    photographs: [{
      path: "original-photographs/specimen.png",
      originalFilename: "specimen.png",
      mediaType: "image/png",
      bytes: 1,
      sha256,
      isUnmodifiedOriginal: true,
    }],
    files: [{ path: "certificate-record.json", role: "record", mediaType: "application/json", bytes: 1, sha256 }],
    signature: {
      algorithm: "Ed25519",
      signedFile: "manifest.json",
      signatureFile: "signature.sig",
      signatureEncoding: "raw 64-byte Ed25519 signature",
    },
    generation: {
      application: "Spacerocks COA Studio",
      version: "1.0.0",
      generatedAt: "2026-07-30T12:00:00.000Z",
      manifestSerialization: "UTF-8, sorted keys, two-space indentation, LF, final newline, no BOM",
    },
  };
}

function v1Manifest() {
  return {
    ...commonManifest(),
    $schema: "coa-manifest-v1.schema.json",
    schemaVersion: "1.0.0",
    packageVersion: 1,
    specimen: {
      meteorite: "Legacy meteorite",
      classification: "L5",
      weightGrams: 12.3,
      weightPrecision: 0.1,
      form: "Fragment",
      numberOfPieces: 1,
      fall: { status: "Find", country: "Canada", locality: "Ottawa" },
      provenance: {},
    },
  };
}

function v2Manifest(identity: "official" | "unclassified" = "unclassified") {
  const official = identity === "official";
  return {
    ...commonManifest(),
    $schema: "coa-manifest-v2.schema.json",
    schemaVersion: "2.0.0",
    packageVersion: 2,
    specimen: {
      meteorite: official ? "Aguas Zarcas" : "Working specimen 001",
      meteoriteIdentity: identity,
      meteoriteType: official ? "Chondrite" : "Unclassified",
      classification: official ? "Carbonaceous chondrite" : "Unclassified",
      meteoriteSubclass: official ? "CM2" : "Unclassified",
      ...(official ? { officialNameVerified: true } : { suspectedType: "Possible chondrite" }),
      weightGrams: 12.3,
      weightPrecision: 0.1,
      form: "Fragment",
      numberOfPieces: 1,
      fall: {
        status: "Find",
        country: "Canada",
        locality: "Ottawa",
        ...(official ? {
          metbullCode: "68063",
          officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063",
        } : {}),
      },
      provenance: {},
    },
  };
}

describe("manifest validation", () => {
  it("validates the committed v1 shape with v1 and both new identity modes with v2", () => {
    expect(validateManifestVersion(v1Manifest())).toMatchObject({ version: "v1", valid: true });
    expect(validateManifestVersion(v2Manifest("unclassified"))).toMatchObject({ version: "v2", valid: true });
    expect(validateManifestVersion(v2Manifest("official"))).toMatchObject({ version: "v2", valid: true });
  });

  it("fails mismatched known identifiers instead of treating them as legacy", () => {
    expect(validateManifestVersion({ ...v1Manifest(), packageVersion: 2 }).version).toBe("mismatch");
    expect(validateManifestVersion({ ...v2Manifest(), $schema: "coa-manifest-v1.schema.json" }).version).toBe("mismatch");
    expect(validateManifestVersion({ schemaVersion: "0.9.0" }).version).toBe("unknown");
  });

  it("provides a final v2 schema assertion", () => {
    expect(() => assertV2Manifest(v2Manifest())).not.toThrow();
    expect(() => assertV2Manifest({ ...v2Manifest(), specimen: { extra: true } })).toThrow(
      "Internal error: generated manifest does not conform to coa-manifest-v2",
    );
    const invalidDate = structuredClone(v2Manifest());
    invalidDate.certificate.issueDate = "2026-02-29";
    expect(() => assertV2Manifest(invalidDate)).toThrow();
    const invalidPhoto = structuredClone(v2Manifest());
    invalidPhoto.photographs[0].mediaType = "application/octet-stream";
    expect(() => assertV2Manifest(invalidPhoto)).toThrow();
    const invalidCaptureDate = structuredClone(v2Manifest());
    Object.assign(invalidCaptureDate.photographs[0], { captureDate: "2026-02-29" });
    expect(() => assertV2Manifest(invalidCaptureDate)).toThrow();
    const invalidCoordinate = structuredClone(v2Manifest());
    Object.assign(invalidCoordinate.specimen.fall, { latitude: "91 N", longitude: "75 N" });
    expect(() => assertV2Manifest(invalidCoordinate)).toThrow();
    for (const classification of ["   ", " Unclassified "]) {
      const invalidClassification = structuredClone(v2Manifest("official"));
      invalidClassification.specimen.classification = classification;
      expect(() => assertV2Manifest(invalidClassification)).toThrow();
    }
  });

  it("semantically binds official evidence code to the sole canonical URL", () => {
    expect(validateOfficialMeteoriteIdentity(v2Manifest("unclassified").specimen).valid).toBe(true);
    expect(validateOfficialMeteoriteIdentity(v2Manifest("official").specimen).valid).toBe(true);
    for (const fall of [
      { metbullCode: "68063", officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68064" },
      { metbullCode: "68063", officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063&extra=1" },
      { metbullCode: "CM2", officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=CM2" },
    ]) {
      expect(validateOfficialMeteoriteIdentity({ ...v2Manifest("official").specimen, fall }).valid).toBe(false);
    }
  });
});
