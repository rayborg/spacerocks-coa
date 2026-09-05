import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import manifestV1Schema from "../../schemas/coa-manifest-v1.schema.json";
import manifestV2Schema from "../../schemas/coa-manifest-v2.schema.json";

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateV1 = ajv.compile(manifestV1Schema);
const validateV2 = ajv.compile(manifestV2Schema);

export type ManifestVersion = "v1" | "v2" | "mismatch" | "unknown";

export interface ManifestValidationResult {
  version: ManifestVersion;
  schemaVersion?: "1.0.0" | "2.0.0" | "2.1.0";
  valid?: boolean;
  errors?: ErrorObject[] | null;
  validator?: ValidateFunction;
}

export function validateManifestVersion(manifest: Record<string, unknown>): ManifestValidationResult {
  const identifiers = [manifest.$schema, manifest.schemaVersion, manifest.packageVersion];
  if (identifiers[0] === "coa-manifest-v1.schema.json" && identifiers[1] === "1.0.0" && identifiers[2] === 1) {
    return { version: "v1", schemaVersion: "1.0.0", valid: validateV1(manifest), errors: validateV1.errors, validator: validateV1 };
  }
  if (
    identifiers[0] === "coa-manifest-v2.schema.json"
    && (identifiers[1] === "2.0.0" || identifiers[1] === "2.1.0")
    && identifiers[2] === 2
  ) {
    return {
      version: "v2",
      schemaVersion: identifiers[1],
      valid: validateV2(manifest),
      errors: validateV2.errors,
      validator: validateV2,
    };
  }

  const usesKnownIdentifier = ["coa-manifest-v1.schema.json", "coa-manifest-v2.schema.json"].includes(String(identifiers[0]))
    || ["1.0.0", "2.0.0", "2.1.0"].includes(String(identifiers[1]))
    || identifiers[2] === 1
    || identifiers[2] === 2;
  return { version: usesKnownIdentifier ? "mismatch" : "unknown" };
}

export function assertV2Manifest(manifest: Record<string, unknown>): void {
  const result = validateManifestVersion(manifest);
  if (result.version === "v2" && result.valid) return;
  const detail = (result.errors ?? [])
    .slice(0, 3)
    .map((item) => `${item.instancePath || "/"} ${item.message}`)
    .join("; ");
  throw new Error(`Internal error: generated manifest does not conform to coa-manifest-v2${detail ? `: ${detail}` : "."}`);
}

export function validateOfficialMeteoriteIdentity(specimen: unknown): { valid: boolean; detail: string } {
  if (!specimen || typeof specimen !== "object") {
    return { valid: false, detail: "The specimen identity record is missing." };
  }
  const record = specimen as Record<string, unknown>;
  if (record.meteoriteIdentity === "unclassified") {
    return { valid: true, detail: "Unclassified specimens do not carry official Meteoritical Bulletin evidence." };
  }
  if (record.meteoriteIdentity !== "official") {
    return { valid: false, detail: "The meteorite identity mode is invalid." };
  }
  const fall = record.fall;
  if (!fall || typeof fall !== "object") {
    return { valid: false, detail: "The official meteorite evidence is missing." };
  }
  const evidence = fall as Record<string, unknown>;
  const code = evidence.metbullCode;
  const url = evidence.officialReferenceUrl;
  if (typeof code !== "string" || !/^[0-9]+$/.test(code)) {
    return { valid: false, detail: "The Meteoritical Bulletin code is not numeric." };
  }
  const expected = `https://www.lpi.usra.edu/meteor/metbull.cfm?code=${code}`;
  if (url !== expected) {
    return { valid: false, detail: `The official reference must be exactly ${expected}.` };
  }
  return { valid: true, detail: `The canonical Meteoritical Bulletin URL matches code ${code}.` };
}
