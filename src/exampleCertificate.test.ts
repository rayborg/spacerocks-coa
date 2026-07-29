import { describe, expect, it } from "vitest";
import { exampleCertificateFieldGroups, exampleCertificateValues } from "./exampleCertificate";

describe("example certificate", () => {
  it("populates and discloses every form field exactly once", () => {
    expect(Object.values(exampleCertificateValues).every((value) => value.trim().length > 0)).toBe(true);

    const disclosedKeys = exampleCertificateFieldGroups.flatMap((group) => group.fields.map((field) => field.key));
    expect(new Set(disclosedKeys).size).toBe(disclosedKeys.length);
    expect([...disclosedKeys].sort()).toEqual(Object.keys(exampleCertificateValues).sort());
  });

  it("is unmistakably synthetic", () => {
    expect(exampleCertificateValues.certificateId).toMatch(/^DEMO-/);
    expect(exampleCertificateValues.metbullCode).toBe("DEMO-NOT-REGISTERED");
    expect(exampleCertificateValues.certificateNotes).toMatch(/synthetic demonstration/i);
    expect(exampleCertificateValues.provenance).toMatch(/no real specimen/i);
  });
});
