import { describe, expect, it } from "vitest";
import { validatePhotographMetadata } from "./verifier";

const sha256 = "a".repeat(64);

function manifest(schemaVersion: "2.0.0" | "2.1.0" = "2.1.0") {
  const photo = {
    path: "original-photographs/specimen.png",
    originalFilename: "specimen.png",
    mediaType: "image/png",
    bytes: 123,
    sha256,
    isUnmodifiedOriginal: true,
    ...(schemaVersion === "2.1.0" ? {
      pixelWidth: 561,
      pixelHeight: 456,
      displayCrop: {
        x: 0,
        y: 0,
        width: 560,
        height: 455,
        targetAspect: "112:91",
        algorithm: "center-cover-v1",
      },
    } : {}),
  };
  return {
    schemaVersion,
    photographs: [photo],
    files: [{
      path: photo.path,
      role: "exact original specimen photograph",
      mediaType: photo.mediaType,
      bytes: photo.bytes,
      sha256: photo.sha256,
    }],
  };
}

describe("photograph metadata verification", () => {
  it("accepts legacy 2.0 file binding and strict 2.1 geometry", () => {
    expect(validatePhotographMetadata(manifest("2.0.0")).valid).toBe(true);
    expect(validatePhotographMetadata(
      manifest(),
      new Map([["original-photographs/specimen.png", { pixelWidth: 561, pixelHeight: 456 }]]),
    ).valid).toBe(true);
  });

  it.each(["sha256", "bytes", "mediaType"] as const)("rejects disagreement in %s", (property) => {
    const changed = structuredClone(manifest());
    (changed.photographs[0] as Record<string, unknown>)[property] = property === "bytes" ? 124 : "tampered";
    expect(validatePhotographMetadata(changed).failures).toContain(`photograph 1 ${property} disagrees with its file entry`);
  });

  it("rejects a photograph path without exactly one matching signed file", () => {
    const changed = structuredClone(manifest());
    changed.photographs[0].path = "original-photographs/other.png";
    expect(validatePhotographMetadata(changed).failures)
      .toContain("photograph 1 must match exactly one manifest file entry");
  });

  it("rejects crop ratio, centering, algorithm, bounds, minimum, and area-loss tampering", () => {
    for (const mutate of [
      (value: Record<string, any>) => { value.photographs[0].displayCrop.width = 561; },
      (value: Record<string, any>) => { value.photographs[0].displayCrop.x = 1; },
      (value: Record<string, any>) => { value.photographs[0].displayCrop.algorithm = "other"; },
      (value: Record<string, any>) => { value.photographs[0].pixelWidth = 100001; },
      (value: Record<string, any>) => {
        value.photographs[0].pixelWidth = 559;
        value.photographs[0].pixelHeight = 455;
      },
      (value: Record<string, any>) => {
        value.photographs[0].pixelWidth = 2241;
        value.photographs[0].pixelHeight = 1729;
      },
    ]) {
      const changed = structuredClone(manifest());
      mutate(changed);
      expect(validatePhotographMetadata(changed).valid).toBe(false);
    }
  });

  it("rejects decoded dimensions that differ from the signed record", () => {
    const result = validatePhotographMetadata(
      manifest(),
      new Map([["original-photographs/specimen.png", { pixelWidth: 560, pixelHeight: 455 }]]),
    );
    expect(result.failures).toContain("photograph 1 decoded dimensions disagree with its signed dimensions");
  });
});
