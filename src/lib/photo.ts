import type { DisplayCrop } from "../types";

export const PHOTO_TARGET_ASPECT = "112:91" as const;
export const PHOTO_TARGET_WIDTH = 112;
export const PHOTO_TARGET_HEIGHT = 91;
export const PHOTO_RECOMMENDED_WIDTH = 1120;
export const PHOTO_RECOMMENDED_HEIGHT = 910;
const PHOTO_MAXIMUM_AREA_LOSS_DENOMINATOR = 20;
export const PHOTO_MAXIMUM_DIMENSION = 100_000;
export const PHOTO_CROP_ALGORITHM = "center-cover-v1" as const;

export const SUPPORTED_PHOTO_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

export type PhotoDimensionAnalysis =
  | {
      valid: true;
      pixelWidth: number;
      pixelHeight: number;
      sourceAspect: number;
      matchesTargetAspect: boolean;
      quality: "accepted" | "recommended";
    }
  | {
      valid: false;
      pixelWidth: number;
      pixelHeight: number;
      sourceAspect: number;
      reason: "invalid-dimensions";
    };

export function isSupportedPhotoMimeType(type: string): boolean {
  return (SUPPORTED_PHOTO_MIME_TYPES as readonly string[]).includes(type.toLowerCase());
}

export function matchesPhotoMimeSignature(type: string, bytes: Uint8Array): boolean {
  if (type.toLowerCase() === "image/jpeg") {
    return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  }
  if (type.toLowerCase() === "image/png") {
    const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    return bytes.length >= png.length && png.every((byte, index) => bytes[index] === byte);
  }
  if (type.toLowerCase() === "image/webp") {
    return bytes.length >= 12
      && String.fromCharCode(...bytes.slice(0, 4)) === "RIFF"
      && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  }
  return false;
}

export function analyzePhotoDimensions(pixelWidth: number, pixelHeight: number): PhotoDimensionAnalysis {
  if (
    !Number.isInteger(pixelWidth)
    || !Number.isInteger(pixelHeight)
    || pixelWidth <= 0
    || pixelHeight <= 0
    || pixelWidth > PHOTO_MAXIMUM_DIMENSION
    || pixelHeight > PHOTO_MAXIMUM_DIMENSION
  ) {
    return {
      valid: false,
      pixelWidth,
      pixelHeight,
      sourceAspect: Number.NaN,
      reason: "invalid-dimensions",
    };
  }

  return {
    valid: true,
    pixelWidth,
    pixelHeight,
    sourceAspect: pixelWidth / pixelHeight,
    matchesTargetAspect: pixelWidth * PHOTO_TARGET_HEIGHT === pixelHeight * PHOTO_TARGET_WIDTH,
    quality: pixelWidth >= PHOTO_RECOMMENDED_WIDTH
      && pixelHeight >= PHOTO_RECOMMENDED_HEIGHT
      && pixelWidth * PHOTO_TARGET_HEIGHT === pixelHeight * PHOTO_TARGET_WIDTH
      ? "recommended"
      : "accepted",
  };
}

export function describePhotoAnalysis(analysis: PhotoDimensionAnalysis): string {
  const dimensions = `${analysis.pixelWidth} x ${analysis.pixelHeight} px`;
  const ratio = Number.isFinite(analysis.sourceAspect) ? analysis.sourceAspect.toFixed(4) : "invalid";
  if (analysis.valid) {
    const quality = analysis.quality === "recommended"
      ? "Optimal 112:91 landscape recommendation met."
      : "Accepted; 112:91 landscape at 1120 x 910 px or larger is recommended, and higher resolution improves print quality.";
    return `${dimensions}; source ratio ${ratio}. The complete image will be centered and contained without cropping, stretching, or distortion. ${quality}`;
  }
  return `The image reports invalid or unsafe pixel dimensions. Each dimension must be between 1 and ${PHOTO_MAXIMUM_DIMENSION.toLocaleString("en-US")} px.`;
}

export function matchesLegacyRecordedCrop(
  pixelWidth: number,
  pixelHeight: number,
  displayCrop: DisplayCrop | undefined,
): boolean {
  if (!Number.isInteger(pixelWidth) || !Number.isInteger(pixelHeight) || pixelWidth <= 0 || pixelHeight <= 0) return false;
  const scale = Math.floor(Math.min(pixelWidth / PHOTO_TARGET_WIDTH, pixelHeight / PHOTO_TARGET_HEIGHT));
  const cropWidth = PHOTO_TARGET_WIDTH * scale;
  const cropHeight = PHOTO_TARGET_HEIGHT * scale;
  const sourceArea = pixelWidth * pixelHeight;
  const cropArea = cropWidth * cropHeight;
  const suitable = scale >= 5 && (sourceArea - cropArea) * PHOTO_MAXIMUM_AREA_LOSS_DENOMINATOR <= sourceArea;
  if (!displayCrop) return !suitable;
  if (!suitable) return false;
  const expected: DisplayCrop = {
    x: Math.floor((pixelWidth - cropWidth) / 2),
    y: Math.floor((pixelHeight - cropHeight) / 2),
    width: cropWidth,
    height: cropHeight,
    targetAspect: PHOTO_TARGET_ASPECT,
    algorithm: PHOTO_CROP_ALGORITHM,
  };
  return Object.entries(expected).every(
    ([key, value]) => displayCrop[key as keyof DisplayCrop] === value,
  );
}
