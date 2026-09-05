import { describe, expect, it } from "vitest";
import { fitImageWithin } from "./certificate";

describe("fitImageWithin", () => {
  it.each([
    [400, 400, 184, 184],
    [800, 120, 320, 48],
    [120, 800, 27.6, 184],
  ])("contains %s x %s without changing its ratio", (sourceWidth, sourceHeight, expectedWidth, expectedHeight) => {
    const fitted = fitImageWithin(sourceWidth, sourceHeight, 320, 184);
    expect(fitted.width).toBeCloseTo(expectedWidth, 5);
    expect(fitted.height).toBeCloseTo(expectedHeight, 5);
    expect(fitted.width / fitted.height).toBeCloseTo(sourceWidth / sourceHeight, 8);
    expect(fitted.width).toBeLessThanOrEqual(320);
    expect(fitted.height).toBeLessThanOrEqual(184);
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
