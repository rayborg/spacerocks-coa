import { describe, expect, it } from "vitest";
import {
  certificateStyleIds,
  certificateStyles,
  getCertificateStyle,
} from "./certificateStyles";

describe("certificate styles", () => {
  it("provides exactly the three supported layouts", () => {
    expect(certificateStyleIds).toEqual([
      "regal-archive",
      "museum-ledger",
      "celestial-formal",
    ]);
    expect(certificateStyles.map(({ name }) => name)).toEqual([
      "Regal Archive",
      "Museum Ledger",
      "Celestial Formal",
    ]);
    expect(new Set(certificateStyles.map(({ id }) => id)).size).toBe(3);
  });

  it("resolves each selectable style", () => {
    for (const id of certificateStyleIds) {
      expect(getCertificateStyle(id).id).toBe(id);
      expect(getCertificateStyle(id).description).not.toBe("");
    }
  });

  it("describes a distinct visual foundation for every style", () => {
    expect(getCertificateStyle("regal-archive").description).toMatch(/engraved|ceremonial/i);
    expect(getCertificateStyle("museum-ledger").description).toMatch(/accession-grid|institutional/i);
    expect(getCertificateStyle("celestial-formal").description).toMatch(/orbital|star-map/i);
    expect(new Set(certificateStyles.map(({ description }) => description)).size).toBe(3);
  });
});
