import { describe, expect, it } from "vitest";
import {
  PHOTO_MAXIMUM_AREA_LOSS,
  analyzePhotoDimensions,
  matchesPhotoMimeSignature,
  matchesRecordedCrop,
} from "./photo";

describe("certificate photo crop", () => {
  it("records an exact deterministic 112:91 crop", () => {
    const result = analyzePhotoDimensions(1120, 910);
    expect(result).toEqual(expect.objectContaining({
      valid: true,
      areaLoss: 0,
      quality: "recommended",
      displayCrop: {
        x: 0,
        y: 0,
        width: 1120,
        height: 910,
        targetAspect: "112:91",
        algorithm: "center-cover-v1",
      },
    }));
  });

  it.each([
    [2240, 1729, { x: 56, y: 0, width: 2128, height: 1729 }],
    [2128, 1820, { x: 0, y: 45, width: 2128, height: 1729 }],
  ])("accepts the exact five-percent boundary for %d x %d", (width, height, crop) => {
    const result = analyzePhotoDimensions(width, height);
    expect(result.valid).toBe(true);
    if (result.valid) {
      expect(result.displayCrop).toEqual(expect.objectContaining(crop));
      expect(result.areaLoss).toBe(PHOTO_MAXIMUM_AREA_LOSS);
    }
  });

  it.each([[2241, 1729], [2128, 1821]])("rejects %d x %d just outside the crop-loss threshold", (width, height) => {
    const result = analyzePhotoDimensions(width, height);
    expect(result).toEqual(expect.objectContaining({ valid: false, reason: "excessive-crop" }));
    expect(result.areaLoss).toBeGreaterThan(PHOTO_MAXIMUM_AREA_LOSS);
  });

  it("uses an exact-ratio integer crop and deterministic odd-pixel placement", () => {
    const result = analyzePhotoDimensions(561, 456);
    expect(result.valid).toBe(true);
    if (!result.valid) return;
    expect(result.displayCrop).toEqual({
      x: 0,
      y: 0,
      width: 560,
      height: 455,
      targetAspect: "112:91",
      algorithm: "center-cover-v1",
    });
    expect(result.displayCrop.width * 91).toBe(result.displayCrop.height * 112);
  });

  it("rejects a crop below the minimum pixel dimensions", () => {
    expect(analyzePhotoDimensions(559, 455)).toEqual(expect.objectContaining({
      valid: false,
      reason: "undersized",
    }));
    expect(analyzePhotoDimensions(560, 454)).toEqual(expect.objectContaining({
      valid: false,
      reason: "undersized",
    }));
  });

  it("matches only the crop derived from the recorded source dimensions", () => {
    const result = analyzePhotoDimensions(1150, 910);
    expect(result.valid).toBe(true);
    if (!result.valid) return;
    expect(matchesRecordedCrop(1150, 910, result.displayCrop)).toBe(true);
    expect(matchesRecordedCrop(1150, 910, { ...result.displayCrop, x: result.displayCrop.x + 1 })).toBe(false);
    expect(matchesRecordedCrop(600, 455, undefined)).toBe(true);
  });

  it("requires the selected MIME type to match the encoded image signature", () => {
    expect(matchesPhotoMimeSignature("image/png", new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe(true);
    expect(matchesPhotoMimeSignature("image/jpeg", new Uint8Array([0xff, 0xd8, 0xff]))).toBe(true);
    expect(matchesPhotoMimeSignature("image/webp", new TextEncoder().encode("RIFF1234WEBP"))).toBe(true);
    expect(matchesPhotoMimeSignature("image/png", new Uint8Array([0xff, 0xd8, 0xff]))).toBe(false);
    expect(matchesPhotoMimeSignature("image/gif", new TextEncoder().encode("GIF89a"))).toBe(false);
  });
});
