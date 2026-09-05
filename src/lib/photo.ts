import type { DisplayCrop } from "../types";

export const PHOTO_TARGET_ASPECT = "112:91" as const;
export const PHOTO_TARGET_WIDTH = 112;
export const PHOTO_TARGET_HEIGHT = 91;
export const PHOTO_MINIMUM_WIDTH = 560;
export const PHOTO_MINIMUM_HEIGHT = 455;
export const PHOTO_RECOMMENDED_WIDTH = 1120;
export const PHOTO_RECOMMENDED_HEIGHT = 910;
const PHOTO_MAXIMUM_AREA_LOSS_DENOMINATOR = 20;
export const PHOTO_MAXIMUM_AREA_LOSS = 1 / PHOTO_MAXIMUM_AREA_LOSS_DENOMINATOR;
export const PHOTO_MAXIMUM_DIMENSION = 100_000;
export const PHOTO_CROP_ALGORITHM = "center-cover-v1" as const;

export const SUPPORTED_PHOTO_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

export type PhotoDimensionAnalysis =
  | {
      valid: true;
      pixelWidth: number;
      pixelHeight: number;
      sourceAspect: number;
      areaLoss: number;
      displayCrop: DisplayCrop;
      quality: "minimum" | "recommended";
    }
  | {
      valid: false;
      pixelWidth: number;
      pixelHeight: number;
      sourceAspect: number;
      areaLoss: number;
      reason: "invalid-dimensions" | "undersized" | "excessive-crop";
      candidateCrop?: DisplayCrop;
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
      areaLoss: 1,
      reason: "invalid-dimensions",
    };
  }

  const scale = Math.floor(Math.min(pixelWidth / PHOTO_TARGET_WIDTH, pixelHeight / PHOTO_TARGET_HEIGHT));
  const cropWidth = PHOTO_TARGET_WIDTH * scale;
  const cropHeight = PHOTO_TARGET_HEIGHT * scale;
  const sourceArea = pixelWidth * pixelHeight;
  const cropArea = cropWidth * cropHeight;
  const lostArea = sourceArea - cropArea;
  const areaLoss = lostArea / sourceArea;
  const candidateCrop: DisplayCrop = {
    // Floor keeps the crop centered and assigns any unmatched odd pixel to the right/bottom edge.
    x: Math.floor((pixelWidth - cropWidth) / 2),
    y: Math.floor((pixelHeight - cropHeight) / 2),
    width: cropWidth,
    height: cropHeight,
    targetAspect: PHOTO_TARGET_ASPECT,
    algorithm: PHOTO_CROP_ALGORITHM,
  };

  if (scale < PHOTO_MINIMUM_WIDTH / PHOTO_TARGET_WIDTH) {
    return {
      valid: false,
      pixelWidth,
      pixelHeight,
      sourceAspect: pixelWidth / pixelHeight,
      areaLoss,
      reason: "undersized",
      candidateCrop,
    };
  }

  if (lostArea * PHOTO_MAXIMUM_AREA_LOSS_DENOMINATOR > sourceArea) {
    return {
      valid: false,
      pixelWidth,
      pixelHeight,
      sourceAspect: pixelWidth / pixelHeight,
      areaLoss,
      reason: "excessive-crop",
      candidateCrop,
    };
  }

  return {
    valid: true,
    pixelWidth,
    pixelHeight,
    sourceAspect: pixelWidth / pixelHeight,
    areaLoss,
    displayCrop: candidateCrop,
    quality: cropWidth >= PHOTO_RECOMMENDED_WIDTH && cropHeight >= PHOTO_RECOMMENDED_HEIGHT
      ? "recommended"
      : "minimum",
  };
}

export function describePhotoAnalysis(analysis: PhotoDimensionAnalysis): string {
  const dimensions = `${analysis.pixelWidth} x ${analysis.pixelHeight} px`;
  const ratio = Number.isFinite(analysis.sourceAspect) ? analysis.sourceAspect.toFixed(4) : "invalid";
  const loss = `${(analysis.areaLoss * 100).toFixed(2)}%`;
  if (analysis.valid) {
    const crop = analysis.displayCrop;
    const quality = analysis.quality === "recommended" ? "recommended resolution met" : "minimum resolution met";
    return `${dimensions}; source ratio ${ratio}; ${loss} source-area loss. Valid 112:91 centered display crop ${crop.width} x ${crop.height} px at (${crop.x}, ${crop.y}); ${quality}.`;
  }
  if (analysis.reason === "undersized") {
    return `${dimensions}; source ratio ${ratio}; ${loss} source-area loss. Not suitable for the primary display photo: the largest 112:91 crop is smaller than the 560 x 455 px minimum. Use a larger landscape JPEG, PNG, or WebP.`;
  }
  if (analysis.reason === "excessive-crop") {
    return `${dimensions}; source ratio ${ratio}; ${loss} source-area loss. Not suitable for the primary display photo because more than 5% would be removed. Reframe to 112:91 landscape or within the 5% crop-loss limit.`;
  }
  return "The image reports invalid pixel dimensions. Use a browser-decodable JPEG, PNG, or WebP.";
}

export function matchesRecordedCrop(
  pixelWidth: number,
  pixelHeight: number,
  displayCrop: DisplayCrop | undefined,
): boolean {
  const analysis = analyzePhotoDimensions(pixelWidth, pixelHeight);
  if (!displayCrop) return !analysis.valid;
  if (!analysis.valid) return false;
  return Object.entries(analysis.displayCrop).every(
    ([key, value]) => displayCrop[key as keyof DisplayCrop] === value,
  );
}
