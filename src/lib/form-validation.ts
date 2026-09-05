import { z } from "zod";
import { certificateStyleIds } from "../certificateStyles";
import { certificateThemeIds } from "../certificateThemes";
import type { FormValues } from "../types";

const requiredText = (label: string) => z.string().trim().min(1, `${label} is required.`);
const optionalEmail = z
  .string()
  .trim()
  .refine((value) => !value || z.string().email().safeParse(value).success, "Enter a valid email address.");
const optionalUrl = z
  .string()
  .trim()
  .refine((value) => !value || z.string().url().safeParse(value).success, "Enter a complete URL, including https://.");
export function isValidIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function isValidCoordinate(value: string, axis: "latitude" | "longitude"): boolean {
  const trimmed = value.trim();
  const maximum = axis === "latitude" ? 90 : 180;
  const maximumIntegerDigits = axis === "latitude" ? 2 : 3;
  const inRange = (numeric: string) => {
    const unsigned = numeric.replace(/^[+-]/, "");
    const integer = unsigned.split(".")[0];
    return integer.length <= maximumIntegerDigits && Number(unsigned) <= maximum;
  };
  const cardinal = trimmed.match(/^(\d+(?:\.\d+)?)\s*([A-Za-z])$/);
  if (cardinal) {
    const allowed = axis === "latitude" ? /^[NS]$/i : /^[EW]$/i;
    return allowed.test(cardinal[2]) && inRange(cardinal[1]);
  }
  if (!/^[+-]?\d+(?:\.\d+)?$/.test(trimmed)) return false;
  return inRange(trimmed) && Math.abs(Number(trimmed)) <= maximum;
}

const optionalDate = z
  .string()
  .trim()
  .refine(
    (value) => !value || isValidIsoDate(value),
    "Enter a valid date.",
  );
const requiredDate = (label: string) => z
  .string()
  .trim()
  .refine((value) => isValidIsoDate(value), `${label} must be a valid date in YYYY-MM-DD format.`);
const positiveNumber = z
  .string()
  .trim()
  .refine((value) => Number.isFinite(Number(value)) && Number(value) > 0, "Enter a number greater than zero.");
const nonNegativeNumber = z
  .string()
  .trim()
  .refine((value) => Number.isFinite(Number(value)) && Number(value) >= 0, "Enter zero or a positive number.");
const positiveInteger = z
  .string()
  .trim()
  .refine((value) => /^\d+$/.test(value) && Number(value) >= 1, "Enter a whole number of at least one.");

export const formSchema: z.ZodType<FormValues, FormValues> = z
  .object({
    issuerName: requiredText("Issuer name"),
    collectionName: requiredText("Collection or business name"),
    issuerEmail: optionalEmail,
    issuerPhone: z.string(),
    issuerAddress: z.string(),
    issuerWebsite: optionalUrl,
    certificateId: z
      .string()
      .trim()
      .regex(/^[A-Za-z0-9][A-Za-z0-9._-]{1,119}$/, "Use 2-120 letters, numbers, periods, underscores, or hyphens."),
    issueDate: requiredDate("Issue date"),
    certificateVersion: requiredText("Certificate version"),
    certificateStatus: z.enum(["active", "superseded", "revoked", "transferred"]),
    certificateStyle: z.enum(certificateStyleIds),
    certificateTheme: z.enum(certificateThemeIds),
    supersededCertificateId: z.string(),
    certificateNotes: z.string(),
    meteoriteIdentity: z.enum(["official", "unclassified"]),
    meteoriteName: requiredText("Meteorite name"),
    meteoriteType: z.string(),
    classification: z.string(),
    meteoriteSubclass: z.string(),
    suspectedType: z.string(),
    officialNameVerified: z.boolean(),
    officialClassificationExceptionAttested: z.boolean().optional(),
    weightGrams: positiveNumber,
    weightPrecision: nonNegativeNumber,
    specimenForm: requiredText("Specimen form"),
    dimensions: z.string(),
    numberOfPieces: positiveInteger,
    preparationState: z.string(),
    identifyingMarks: z.string(),
    recordedOwner: z.string(),
    fallStatus: requiredText("Fall or find status"),
    fallDate: optionalDate,
    country: requiredText("Country"),
    region: z.string(),
    locality: z.string(),
    latitude: z.string(),
    longitude: z.string(),
    metbullCode: z.string(),
    officialReferenceUrl: z.string(),
    finderName: z.string(),
    recoveryInformation: z.string(),
    provenance: z.string(),
    previousOwner: z.string(),
    intermediaryPurchaserName: z.string(),
    buyer: z.string(),
    transferDate: optionalDate,
    invoiceReference: z.string(),
    transferNotes: z.string(),
  })
  .superRefine((values, context) => {
    if (values.certificateStatus === "superseded" && !values.supersededCertificateId.trim()) {
      context.addIssue({
        code: "custom",
        path: ["supersededCertificateId"],
        message: "Record the certificate ID this version supersedes.",
      });
    }

    const locality = values.locality.trim();
    const region = values.region.trim();
    const latitude = values.latitude.trim();
    const longitude = values.longitude.trim();
    if (Boolean(latitude) !== Boolean(longitude)) {
      context.addIssue({
        code: "custom",
        path: [latitude ? "longitude" : "latitude"],
        message: "Enter both latitude and longitude, or leave both blank.",
      });
    } else if (latitude && longitude) {
      if (!isValidCoordinate(latitude, "latitude")) {
        context.addIssue({
          code: "custom",
          path: ["latitude"],
          message: "Enter latitude as signed decimal degrees or unsigned decimal degrees with N/S (range 0-90).",
        });
      }
      if (!isValidCoordinate(longitude, "longitude")) {
        context.addIssue({
          code: "custom",
          path: ["longitude"],
          message: "Enter longitude as signed decimal degrees or unsigned decimal degrees with E/W (range 0-180).",
        });
      }
    } else if (!locality && !region) {
      context.addIssue({
        code: "custom",
        path: ["locality"],
        message: "Enter a locality, region, or complete latitude and longitude.",
      });
    }

    if (values.meteoriteIdentity !== "official") return;

    for (const [field, label] of [["classification", "Meteorite class"]] as const) {
      const value = values[field].trim();
      if (!value) {
        context.addIssue({ code: "custom", path: [field], message: `${label} is required for an official meteorite.` });
      } else if (value.toLowerCase() === "unclassified") {
        context.addIssue({ code: "custom", path: [field], message: `${label} cannot be Unclassified in official mode.` });
      }
    }
    const missingClassificationDetails = (["meteoriteType", "meteoriteSubclass"] as const).filter((field) => {
      const value = values[field].trim();
      return !value || ["unclassified", "n/a", "na", "not applicable"].includes(value.toLowerCase());
    });
    if (missingClassificationDetails.length && !values.officialClassificationExceptionAttested) {
      for (const field of missingClassificationDetails) {
        context.addIssue({
          code: "custom",
          path: [field],
          message: "Enter the official value or attest below that the linked MetBull entry does not provide it.",
        });
      }
    }
    if (!missingClassificationDetails.length && values.officialClassificationExceptionAttested) {
      context.addIssue({
        code: "custom",
        path: ["officialClassificationExceptionAttested"],
        message: "Use this exception only when the official entry omits the type or subclass.",
      });
    }

    const metbullCode = values.metbullCode.trim();
    if (!/^\d+$/.test(metbullCode)) {
      context.addIssue({
        code: "custom",
        path: ["metbullCode"],
        message: "Enter the numeric Meteoritical Bulletin code.",
      });
    }
    const expectedUrl = `https://www.lpi.usra.edu/meteor/metbull.cfm?code=${metbullCode}`;
    if (values.officialReferenceUrl.trim() !== expectedUrl) {
      context.addIssue({
        code: "custom",
        path: ["officialReferenceUrl"],
        message: "Enter the exact Meteoritical Bulletin URL for this code.",
      });
    }
    if (!values.officialNameVerified) {
      context.addIssue({
        code: "custom",
        path: ["officialNameVerified"],
        message: "Attest that the official name and classification match the linked Meteoritical Bulletin entry.",
      });
    }
  });

export function validateFormValues(values: FormValues): FormValues {
  const result = formSchema.safeParse(values);
  if (result.success) return result.data;
  const firstIssue = result.error.issues[0];
  throw new Error(`Certificate form is invalid: ${firstIssue?.message ?? "Review the required fields."}`);
}
