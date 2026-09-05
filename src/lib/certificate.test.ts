import { describe, expect, it } from "vitest";
import { fitImageWithin, formatCertificateLocation } from "./certificate";

describe("fitImageWithin", () => {
  it.each([
    [400, 400, 184, 184],
    [800, 120, 320, 48],
    [120, 800, 27.6, 184],
  ])("contains %s x %s without changing its ratio", (sourceWidth, sourceHeight, expectedWidth, expectedHeight) => {
    const fitted = fitImageWithin(sourceWidth, sourceHeight, 320, 184);
    expect(fitted).toEqual(expect.objectContaining({
      x: (320 - expectedWidth) / 2,
      y: (184 - expectedHeight) / 2,
      width: expectedWidth,
      height: expectedHeight,
    }));
    expect(fitted.width / fitted.height).toBeCloseTo(sourceWidth / sourceHeight, 8);
    expect(fitted.width).toBeLessThanOrEqual(320);
    expect(fitted.height).toBeLessThanOrEqual(184);
  });

  it.each([
    [100, 100, 52.5, 0, 455, 455],
    [100_000, 1, 0, 227.4972, 560, 0.0056],
    [1, 100_000, 279.997725, 0, 0.00455, 455],
    [20, 10, 0, 87.5, 560, 280],
  ])("centers square, extreme, and low-resolution sources exactly", (sourceWidth, sourceHeight, x, y, width, height) => {
    expect(fitImageWithin(sourceWidth, sourceHeight, 560, 455)).toEqual({ x, y, width, height });
  });

  it("increases representative logo area by at least three times the previous export boxes", () => {
    for (const [sourceWidth, sourceHeight] of [[400, 400], [800, 120], [120, 800]]) {
      const previous = fitImageWithin(sourceWidth, sourceHeight, 120, 105);
      const enlarged = fitImageWithin(sourceWidth, sourceHeight, 320, 184);
      expect(enlarged.width * enlarged.height).toBeGreaterThanOrEqual(previous.width * previous.height * 3);
    }
  });

  it("rejects invalid source or frame dimensions", () => {
    expect(() => fitImageWithin(0, 10, 100, 100)).toThrow(/positive/);
    expect(() => fitImageWithin(10, 10, -1, 100)).toThrow(/positive/);
  });
});

describe("formatCertificateLocation", () => {
  it("omits blank, duplicate, and literal None location parts", () => {
    expect(formatCertificateLocation({ locality: "None", region: "not applicable", country: "Canada" })).toBe("Canada");
    expect(formatCertificateLocation({ locality: "Ottawa", region: "ottawa", country: "Canada" })).toBe("Ottawa, Canada");
  });
});
