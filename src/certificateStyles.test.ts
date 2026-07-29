import { describe, expect, it } from "vitest";
import {
  certificateStyleIds,
  certificateStyles,
  getCertificateStyle,
} from "./certificateStyles";

describe("certificate styles", () => {
  it("accepts four layouts while exposing exactly two for new certificates", () => {
    expect(certificateStyleIds).toEqual([
      "regal-archive",
      "museum-ledger",
      "celestial-formal",
      "museum-type",
    ]);
    expect(certificateStyles.map(({ name }) => name)).toEqual([
      "Celestial Formal",
      "Museum Type",
    ]);
    expect(new Set(certificateStyles.map(({ id }) => id)).size).toBe(2);
  });

  it("resolves every accepted style, including hidden legacy styles", () => {
    for (const id of certificateStyleIds) {
      expect(getCertificateStyle(id).id).toBe(id);
      expect(getCertificateStyle(id).description).not.toBe("");
    }
  });

  it("describes a distinct visual foundation for every style", () => {
    expect(getCertificateStyle("regal-archive").description).toMatch(/engraved|ceremonial/i);
    expect(getCertificateStyle("museum-ledger").description).toMatch(/accession-grid|institutional/i);
    expect(getCertificateStyle("celestial-formal").description).toMatch(/orbital|star-map/i);
    expect(getCertificateStyle("museum-type").description).toMatch(/scientific|identification-card/i);
    expect(new Set(certificateStyleIds.map((id) => getCertificateStyle(id).description)).size).toBe(4);
  });
});
