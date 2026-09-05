import { describe, expect, it } from "vitest";
import type { FormValues, PhotoInput, SigningIdentity } from "../types";
import { formSchema, isValidCoordinate } from "./form-validation";
import { buildCertificatePackage } from "./package";
import { formatCertificateLocation } from "./certificate";

const validUnclassified: FormValues = {
  issuerName: "Test Issuer",
  collectionName: "Test Collection",
  issuerEmail: "",
  issuerPhone: "",
  issuerAddress: "",
  issuerWebsite: "",
  certificateId: "TEST-001",
  issueDate: "2026-07-30",
  certificateVersion: "1.0",
  certificateStatus: "active",
  certificateStyle: "celestial-formal",
  certificateTheme: "observatory-navy",
  supersededCertificateId: "",
  certificateNotes: "",
  meteoriteIdentity: "unclassified",
  meteoriteName: "Working specimen 001",
  meteoriteType: "Unclassified",
  classification: "Unclassified",
  meteoriteSubclass: "Unclassified",
  suspectedType: "",
  officialNameVerified: false,
  weightGrams: "12.3",
  weightPrecision: "0.1",
  specimenForm: "Fragment",
  dimensions: "",
  numberOfPieces: "1",
  preparationState: "",
  identifyingMarks: "",
  recordedOwner: "",
  fallStatus: "Find",
  fallDate: "",
  country: "Canada",
  region: "",
  locality: "Example Township",
  latitude: "",
  longitude: "",
  metbullCode: "",
  officialReferenceUrl: "",
  finderName: "",
  recoveryInformation: "",
  provenance: "",
  previousOwner: "",
  intermediaryPurchaserName: "",
  buyer: "",
  transferDate: "",
  invoiceReference: "",
  transferNotes: "",
};

function officialValues(overrides: Partial<FormValues> = {}): FormValues {
  return {
    ...validUnclassified,
    meteoriteIdentity: "official",
    meteoriteName: "Aguas Zarcas",
    meteoriteType: "Chondrite",
    classification: "Carbonaceous chondrite",
    meteoriteSubclass: "CM2",
    metbullCode: "68063",
    officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063",
    officialNameVerified: true,
    ...overrides,
  };
}

describe("formSchema", () => {
  it("accepts the unclassified defaults for classification and ignores stale official evidence", () => {
    expect(formSchema.safeParse({
      ...validUnclassified,
      metbullCode: "stale-code",
      officialReferenceUrl: "not a URL",
      officialNameVerified: true,
    }).success).toBe(true);
  });

  it("requires complete, classified, self-consistent official evidence", () => {
    expect(formSchema.safeParse(officialValues()).success).toBe(true);
    for (const overrides of [
      { meteoriteType: "Unclassified" },
      { classification: "unclassified" },
      { meteoriteSubclass: "" },
      { metbullCode: "CM2" },
      { officialReferenceUrl: "http://www.lpi.usra.edu/meteor/metbull.cfm?code=68063" },
      { officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68064" },
      { officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063&extra=1" },
      { officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063#entry" },
      { officialNameVerified: false },
    ] satisfies Array<Partial<FormValues>>) {
      expect(formSchema.safeParse(officialValues(overrides)).success, JSON.stringify(overrides)).toBe(false);
    }
  });

  it("allows missing official type or subclass only with the explicit MetBull exception attestation", () => {
    expect(formSchema.safeParse(officialValues({
      meteoriteType: "",
      meteoriteSubclass: "",
      officialClassificationExceptionAttested: true,
    })).success).toBe(true);
    expect(formSchema.safeParse(officialValues({
      meteoriteType: "",
      officialClassificationExceptionAttested: true,
    })).success).toBe(true);
    expect(formSchema.safeParse(officialValues({
      meteoriteSubclass: "N/A",
      officialClassificationExceptionAttested: true,
    })).success).toBe(true);
    expect(formSchema.safeParse(officialValues({
      meteoriteType: "",
      meteoriteSubclass: "",
      officialClassificationExceptionAttested: false,
    })).success).toBe(false);
    expect(formSchema.safeParse(officialValues({
      classification: "Unclassified",
      meteoriteType: "",
      meteoriteSubclass: "",
      officialClassificationExceptionAttested: true,
    })).success).toBe(false);
    expect(formSchema.safeParse(officialValues({
      officialClassificationExceptionAttested: true,
    })).success).toBe(false);
  });

  it("accepts each complete location method and rejects no location or a half coordinate", () => {
    const locations: Array<Partial<FormValues>> = [
      { locality: "Ottawa", region: "", latitude: "", longitude: "" },
      { locality: "", region: "Ontario", latitude: "", longitude: "" },
      { locality: "", region: "", latitude: "45.4 N", longitude: "75.7 W" },
    ];
    for (const location of locations) {
      expect(formSchema.safeParse({ ...validUnclassified, ...location }).success).toBe(true);
    }
    expect(formSchema.safeParse({
      ...validUnclassified,
      locality: "",
      region: "",
      latitude: "",
      longitude: "",
    }).success).toBe(false);
    expect(formSchema.safeParse({ ...validUnclassified, latitude: "45.4 N", longitude: "" }).success).toBe(false);
    expect(formSchema.safeParse({ ...validUnclassified, latitude: "", longitude: "75.7 W" }).success).toBe(false);
  });

  it("validates decimal-degree coordinate syntax, axes, and ranges", () => {
    for (const value of ["0", "-90", "+45.4215", "45.4215 N", " 90 S "]) {
      expect(isValidCoordinate(value, "latitude"), value).toBe(true);
    }
    for (const value of ["0", "-180", "+75.6972", "75.6972 W", "180 E"]) {
      expect(isValidCoordinate(value, "longitude"), value).toBe(true);
    }
    for (const value of ["91", "45 E", "-45 N", "45.2.1", "north 45", "45 N extra"]) {
      expect(isValidCoordinate(value, "latitude"), value).toBe(false);
    }
    for (const value of ["181", "75 N", "+75 W", "75 degrees", "W 75"]) {
      expect(isValidCoordinate(value, "longitude"), value).toBe(false);
    }
  });

  it("formats export locations without duplicating coordinates or repeated components", () => {
    expect(formatCertificateLocation({ locality: "Ottawa", region: "", country: "Canada" })).toBe("Ottawa, Canada");
    expect(formatCertificateLocation({ locality: "", region: "Ontario", country: "Canada" })).toBe("Ontario, Canada");
    expect(formatCertificateLocation({ locality: "", region: "", country: "Canada" })).toBe("Canada");
    expect(formatCertificateLocation({ locality: "Canada", region: "", country: "Canada" })).toBe("Canada");
  });

  it("requires a real issue date", () => {
    for (const issueDate of ["", "2026-02-29", "2026-13-01", "07/30/2026"]) {
      expect(formSchema.safeParse({ ...validUnclassified, issueDate }).success, issueDate).toBe(false);
    }
    expect(formSchema.safeParse({ ...validUnclassified, issueDate: "2024-02-29" }).success).toBe(true);
  });

  it("is enforced by the package builder before photo or rendering work", async () => {
    await expect(buildCertificatePackage({
      values: { ...validUnclassified, country: "" },
      photos: [],
      identity: {} as SigningIdentity,
    })).rejects.toThrow("Certificate form is invalid: Country is required.");
  });

  it("defensively rejects invalid photo MIME, per-photo size, and capture date before reading files", async () => {
    const identity = {} as SigningIdentity;
    const photo = (overrides: Partial<File> & { captureDate?: string }): PhotoInput => ({
      id: "photo-1",
      file: {
        name: "specimen.png",
        type: "image/png",
        size: 1,
        ...overrides,
      } as File,
      previewUrl: "blob:test",
      caption: "",
      captureDate: overrides.captureDate ?? "",
      isUnmodifiedOriginal: true,
      pixelWidth: 560,
      pixelHeight: 455,
    });

    await expect(buildCertificatePackage({
      values: validUnclassified,
      photos: [photo({ type: "application/octet-stream" })],
      identity,
    })).rejects.toThrow("must have an image MIME type");
    await expect(buildCertificatePackage({
      values: validUnclassified,
      photos: [photo({ size: 100 * 1024 * 1024 + 1 })],
      identity,
    })).rejects.toThrow("exceeds the 100 MB per-photo limit");
    await expect(buildCertificatePackage({
      values: validUnclassified,
      photos: [photo({ captureDate: "2026-02-29" })],
      identity,
    })).rejects.toThrow("has an invalid capture date");
  });
});
