import { describe, expect, it } from "vitest";
import { formatFingerprint, sanitizeFileName, sha256Hex, stableStringify, utf8 } from "./core";

describe("stableStringify", () => {
  it("sorts object keys recursively and ends with one newline", () => {
    const result = stableStringify({ z: 1, a: { y: 2, b: 3 }, items: [{ d: 4, c: 5 }] });
    expect(result).toBe(`{\n  "a": {\n    "b": 3,\n    "y": 2\n  },\n  "items": [\n    {\n      "c": 5,\n      "d": 4\n    }\n  ],\n  "z": 1\n}\n`);
  });

  it("omits undefined properties", () => {
    expect(stableStringify({ kept: true, omitted: undefined })).toBe(`{\n  "kept": true\n}\n`);
  });
});

describe("hash and filename utilities", () => {
  it("matches the known SHA-256 digest for abc", async () => {
    expect(await sha256Hex(utf8("abc"))).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("formats fingerprints and portable filenames", () => {
    expect(formatFingerprint("0011aaff")).toBe("00:11:AA:FF");
    expect(sanitizeFileName("../Cut face (original).JPG")).toBe("Cut-face-original-.JPG");
  });
});
