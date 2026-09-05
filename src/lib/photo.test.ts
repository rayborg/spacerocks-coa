import { describe, expect, it } from "vitest";
import {
  PHOTO_MAXIMUM_DIMENSION,
  analyzePhotoDimensions,
  describePhotoAnalysis,
  matchesLegacyRecordedCrop,
  matchesPhotoMimeSignature,
} from "./photo";

describe("certificate photo acceptance", () => {
  it("recognizes the optimal recommendation without making it mandatory", () => {
    expect(analyzePhotoDimensions(1120, 910)).toEqual(expect.objectContaining({
      valid: true,
      matchesTargetAspect: true,
      quality: "recommended",
    }));
    expect(describePhotoAnalysis(analyzePhotoDimensions(1120, 910))).toContain("Optimal 112:91 landscape recommendation met");
  });

  it.each([
    [1, 1, "square low-resolution"],
    [PHOTO_MAXIMUM_DIMENSION, 1, "extreme wide"],
    [1, PHOTO_MAXIMUM_DIMENSION, "extreme tall"],
    [320, 200, "small landscape"],
    [800, 1200, "portrait"],
  ])("accepts a safely bounded %s x %s %s image", (width, height) => {
    const result = analyzePhotoDimensions(width, height);
    expect(result).toEqual(expect.objectContaining({ valid: true, quality: "accepted" }));
    expect(describePhotoAnalysis(result)).toContain("without cropping, stretching, or distortion");
  });

  it.each([
    [0, 1],
    [1, 0],
    [-1, 20],
    [1.5, 20],
    [PHOTO_MAXIMUM_DIMENSION + 1, 1],
    [1, PHOTO_MAXIMUM_DIMENSION + 1],
  ])("rejects invalid or unsafe dimensions %s x %s", (width, height) => {
    expect(analyzePhotoDimensions(width, height)).toEqual(expect.objectContaining({
      valid: false,
      reason: "invalid-dimensions",
    }));
  });

  it("retains strict deterministic crop matching only for legacy 2.1 verification", () => {
    const crop = { x: 0, y: 0, width: 560, height: 455, targetAspect: "112:91", algorithm: "center-cover-v1" } as const;
    expect(matchesLegacyRecordedCrop(561, 456, crop)).toBe(true);
    expect(matchesLegacyRecordedCrop(561, 456, { ...crop, x: 1 })).toBe(false);
    expect(matchesLegacyRecordedCrop(2241, 1729, undefined)).toBe(true);
    expect(matchesLegacyRecordedCrop(2241, 1729, crop)).toBe(false);
  });

  it("requires the selected MIME type to match the encoded image signature", () => {
    expect(matchesPhotoMimeSignature("image/png", new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe(true);
    expect(matchesPhotoMimeSignature("image/jpeg", new Uint8Array([0xff, 0xd8, 0xff]))).toBe(true);
    expect(matchesPhotoMimeSignature("image/webp", new TextEncoder().encode("RIFF1234WEBP"))).toBe(true);
    expect(matchesPhotoMimeSignature("image/png", new Uint8Array([0xff, 0xd8, 0xff]))).toBe(false);
    expect(matchesPhotoMimeSignature("image/gif", new TextEncoder().encode("GIF89a"))).toBe(false);
  });
});
