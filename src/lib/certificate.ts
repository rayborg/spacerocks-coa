import { jsPDF } from "jspdf";
import QRCode from "qrcode";
import type { FormValues } from "../types";
import { certificateFooterLayout, getCertificateTheme } from "../certificateThemes";
import type { CertificateStyleId } from "../certificateStyles";
import { displayDate } from "./core";

const WIDTH = 2200;
const HEIGHT = 1700;
const CELESTIAL_PHOTO_ASPECT_RATIO = 560 / 455;
export const CERTIFICATE_EXPORT_FONT_FLOOR = 16;

function recorded(value: string): string {
  return value.trim() || "Not recorded";
}

function recordedCoordinates(latitude: string, longitude: string): string {
  return [latitude.trim(), longitude.trim()].filter(Boolean).join(", ") || "Not recorded";
}

function classificationSummary(values: FormValues): string {
  if (values.meteoriteIdentity === "unclassified") {
    return values.suspectedType.trim() ? `Unclassified - suspected ${values.suspectedType.trim()}` : "Unclassified";
  }
  return [values.meteoriteType, values.classification, values.meteoriteSubclass]
    .map((value, index) => value.trim() || (values.officialClassificationExceptionAttested && index !== 1 ? "Not separately provided in MetBull" : ""))
    .filter(Boolean)
    .join(" / ");
}

export function formatCertificateLocation(values: Pick<FormValues, "locality" | "region" | "country">): string {
  const parts = [values.locality, values.region, values.country]
    .map((value) => value.trim())
    .filter((value, index, all) => value && all.findIndex((candidate) => candidate.toLowerCase() === value.toLowerCase()) === index);
  return parts.join(", ") || "Not recorded";
}

export interface CertificateRenderInput {
  values: FormValues;
  fingerprint: string;
  recordHash: string;
  qrPayload: string;
  mainPhoto: File;
  logo?: File;
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const safeRadius = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + safeRadius, y);
  context.lineTo(x + width - safeRadius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
  context.lineTo(x + width, y + height - safeRadius);
  context.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
  context.lineTo(x + safeRadius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
  context.lineTo(x, y + safeRadius);
  context.quadraticCurveTo(x, y, x + safeRadius, y);
  context.closePath();
}

function fitFontSize(
  context: CanvasRenderingContext2D,
  text: string,
  maximumWidth: number,
  startingSize: number,
  family: string,
  weight = "400",
) {
  let size = startingSize;
  while (size > CERTIFICATE_EXPORT_FONT_FLOOR) {
    context.font = `${weight} ${size}px ${family}`;
    if (context.measureText(text).width <= maximumWidth) return size;
    size -= 2;
  }
  return size;
}

function fitTextWithEllipsis(context: CanvasRenderingContext2D, text: string, maximumWidth: number) {
  if (context.measureText(text).width <= maximumWidth) return text;
  const suffix = "...";
  let low = 0;
  let high = text.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    const candidate = `${text.slice(0, middle).trimEnd()}${suffix}`;
    if (context.measureText(candidate).width <= maximumWidth) low = middle;
    else high = middle - 1;
  }
  return `${text.slice(0, low).trimEnd()}${suffix}`;
}

function wrapText(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines = 3,
) {
  let remaining = text.trim().replace(/\s+/g, " ");
  const lines: string[] = [];

  while (remaining) {
    if (context.measureText(remaining).width <= maxWidth) {
      lines.push(remaining);
      break;
    }

    let low = 0;
    let high = remaining.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (context.measureText(remaining.slice(0, middle)).width <= maxWidth) low = middle;
      else high = middle - 1;
    }
    if (low === 0) break;

    const fittingPrefix = remaining.slice(0, low);
    const whitespaceIndex = fittingPrefix.lastIndexOf(" ");
    const splitIndex = whitespaceIndex > 0 ? whitespaceIndex : low;
    lines.push(remaining.slice(0, splitIndex).trimEnd());
    remaining = remaining.slice(splitIndex).trimStart();
  }

  const visible = lines.slice(0, maxLines);
  if (lines.length > maxLines) {
    const lastIndex = maxLines - 1;
    visible[lastIndex] = fitTextWithEllipsis(context, `${visible[lastIndex] ?? ""}...`, maxWidth);
  }

  visible.forEach((value, index) => context.fillText(value, x, y + index * lineHeight));
  return visible.length;
}

async function fileToImage(file: File): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function dataUrlToImage(url: string): Promise<HTMLImageElement> {
  const image = new Image();
  image.src = url;
  await image.decode();
  return image;
}

function drawContainedImage(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  x: number,
  y: number,
  width: number,
  height: number,
) {
  const fitted = fitImageWithin(
    image.naturalWidth,
    image.naturalHeight,
    width,
    height,
  );
  context.drawImage(image, x + fitted.x, y + fitted.y, fitted.width, fitted.height);
}

export function fitImageWithin(
  sourceWidth: number,
  sourceHeight: number,
  maximumWidth: number,
  maximumHeight: number,
): { x: number; y: number; width: number; height: number } {
  if (sourceWidth <= 0 || sourceHeight <= 0 || maximumWidth <= 0 || maximumHeight <= 0) {
    throw new Error("Image and frame dimensions must be positive.");
  }
  const scale = Math.min(maximumWidth / sourceWidth, maximumHeight / sourceHeight);
  const width = sourceWidth * scale;
  const height = sourceHeight * scale;
  return {
    x: (maximumWidth - width) / 2,
    y: (maximumHeight - height) / 2,
    width,
    height,
  };
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The certificate PNG could not be rendered."));
    }, "image/png");
  });
}

function drawOrbitMark(context: CanvasRenderingContext2D, x: number, y: number, color: string) {
  context.save();
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 4;
  context.beginPath();
  context.ellipse(x, y, 48, 17, -0.3, 0, Math.PI * 2);
  context.stroke();
  context.beginPath();
  context.arc(x, y, 15, 0, Math.PI * 2);
  context.fill();
  context.beginPath();
  context.arc(x + 42, y - 15, 6, 0, Math.PI * 2);
  context.fill();
  context.restore();
}

function drawLedgerMark(context: CanvasRenderingContext2D, x: number, y: number, color: string) {
  context.save();
  context.strokeStyle = color;
  context.lineWidth = 3;
  context.beginPath();
  context.arc(x, y, 40, 0, Math.PI * 2);
  context.stroke();
  context.beginPath();
  context.arc(x, y, 31, 0, Math.PI * 2);
  context.stroke();
  context.lineWidth = 5;
  context.beginPath();
  context.moveTo(x - 19, y + 16);
  context.lineTo(x - 19, y - 15);
  context.lineTo(x, y + 7);
  context.lineTo(x + 19, y - 15);
  context.lineTo(x + 19, y + 16);
  context.stroke();
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(x - 24, y + 22);
  context.lineTo(x + 24, y + 22);
  context.stroke();
  context.restore();
}

function drawStyleFoundation(
  context: CanvasRenderingContext2D,
  style: CertificateStyleId,
  dark: string,
  accent: string,
) {
  context.save();
  if (style === "museum-ledger") {
    context.fillStyle = accent;
    context.fillRect(74, 410, 8, 1150);
    context.fillStyle = dark;
    context.fillRect(88, 410, 3, 1150);
    context.strokeStyle = `${dark}24`;
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(112, 650);
    context.lineTo(2070, 650);
    context.moveTo(112, 1270);
    context.lineTo(2070, 1270);
    context.stroke();
    context.globalAlpha = 0.08;
    context.lineWidth = 1;
    for (let x = 112; x <= 2070; x += 164) {
      context.beginPath();
      context.moveTo(x, 410);
      context.lineTo(x, 1560);
      context.stroke();
    }
    for (let y = 410; y <= 1560; y += 82) {
      context.beginPath();
      context.moveTo(112, y);
      context.lineTo(2070, y);
      context.stroke();
    }
    context.globalAlpha = 0.4;
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(1990, 1475);
    context.lineTo(2050, 1475);
    context.moveTo(2020, 1445);
    context.lineTo(2020, 1505);
    context.stroke();
  } else if (style === "celestial-formal") {
    context.strokeStyle = `${accent}3d`;
    context.fillStyle = `${dark}26`;
    context.lineWidth = 5;
    for (const [radiusX, radiusY, rotation] of [
      [610, 330, -0.34],
      [455, 245, 0.28],
      [320, 165, -0.15],
    ] as const) {
      context.beginPath();
      context.ellipse(1710, 1040, radiusX, radiusY, rotation, 0, Math.PI * 2);
      context.stroke();
    }
    for (const [x, y, radius] of [
      [1315, 1198, 11],
      [2015, 858, 7],
      [1570, 770, 5],
      [1885, 1320, 8],
      [1190, 930, 4],
    ] as const) {
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
    }
    context.setLineDash([10, 14]);
    context.beginPath();
    context.moveTo(1188, 930);
    context.lineTo(1570, 770);
    context.lineTo(2015, 858);
    context.stroke();
  } else {
    context.globalAlpha = 0.12;
    context.strokeStyle = accent;
    context.lineWidth = 2;
    for (let x = 120; x <= 2080; x += 44) {
      context.beginPath();
      context.moveTo(x, 405);
      context.lineTo(x + 22, 425);
      context.lineTo(x, 445);
      context.lineTo(x - 22, 425);
      context.closePath();
      context.stroke();
      context.beginPath();
      context.moveTo(x, 1518);
      context.lineTo(x + 22, 1538);
      context.lineTo(x, 1558);
      context.lineTo(x - 22, 1538);
      context.closePath();
      context.stroke();
    }
  }
  context.restore();
}

function drawStyleFrame(
  context: CanvasRenderingContext2D,
  style: CertificateStyleId,
  dark: string,
  accent: string,
) {
  context.save();
  if (style === "museum-ledger") {
    context.strokeStyle = dark;
    context.lineWidth = 8;
    context.strokeRect(28, 28, WIDTH - 56, HEIGHT - 56);
    context.strokeStyle = accent;
    context.lineWidth = 3;
    context.strokeRect(44, 44, WIDTH - 88, HEIGHT - 88);
    context.strokeStyle = dark;
    context.lineWidth = 2;
    context.strokeRect(57, 57, WIDTH - 114, HEIGHT - 114);
    context.beginPath();
    context.moveTo(57, 372);
    context.lineTo(WIDTH - 57, 372);
    context.moveTo(57, 384);
    context.lineTo(WIDTH - 57, 384);
    context.stroke();
    context.lineWidth = 3;
    for (const [x, y] of [[84, 84], [WIDTH - 84, 84], [84, HEIGHT - 84], [WIDTH - 84, HEIGHT - 84]] as const) {
      context.beginPath();
      context.moveTo(x - 22, y);
      context.lineTo(x + 22, y);
      context.moveTo(x, y - 22);
      context.lineTo(x, y + 22);
      context.stroke();
    }
  } else if (style === "celestial-formal") {
    context.strokeStyle = accent;
    context.lineWidth = 5;
    roundedRect(context, 27, 27, WIDTH - 54, HEIGHT - 54, 30);
    context.stroke();
    context.strokeStyle = dark;
    context.lineWidth = 2;
    roundedRect(context, 43, 43, WIDTH - 86, HEIGHT - 86, 22);
    context.stroke();
  } else {
    context.strokeStyle = accent;
    context.lineWidth = 10;
    context.strokeRect(25, 25, WIDTH - 50, HEIGHT - 50);
    context.lineWidth = 3;
    context.strokeRect(42, 42, WIDTH - 84, HEIGHT - 84);
    context.strokeStyle = dark;
    context.lineWidth = 2;
    context.strokeRect(54, 54, WIDTH - 108, HEIGHT - 108);
    context.strokeStyle = accent;
    const cornerLength = 104;
    for (const [x, y, xDirection, yDirection] of [
      [66, 66, 1, 1],
      [WIDTH - 66, 66, -1, 1],
      [66, HEIGHT - 66, 1, -1],
      [WIDTH - 66, HEIGHT - 66, -1, -1],
    ] as const) {
      context.lineWidth = 5;
      context.beginPath();
      context.moveTo(x + xDirection * cornerLength, y);
      context.lineTo(x, y);
      context.lineTo(x, y + yDirection * cornerLength);
      context.stroke();
      context.save();
      context.translate(x + xDirection * 28, y + yDirection * 28);
      context.rotate(Math.PI / 4);
      context.strokeRect(-10, -10, 20, 20);
      context.restore();
    }
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(WIDTH / 2 - 150, 54);
    context.lineTo(WIDTH / 2 - 22, 54);
    context.moveTo(WIDTH / 2 + 22, 54);
    context.lineTo(WIDTH / 2 + 150, 54);
    context.moveTo(WIDTH / 2 - 150, HEIGHT - 54);
    context.lineTo(WIDTH / 2 - 22, HEIGHT - 54);
    context.moveTo(WIDTH / 2 + 22, HEIGHT - 54);
    context.lineTo(WIDTH / 2 + 150, HEIGHT - 54);
    context.stroke();
    for (const y of [54, HEIGHT - 54]) {
      context.save();
      context.translate(WIDTH / 2, y);
      context.rotate(Math.PI / 4);
      context.strokeRect(-10, -10, 20, 20);
      context.restore();
    }
  }
  context.restore();
}

async function drawMuseumTypeCertificate(
  context: CanvasRenderingContext2D,
  input: CertificateRenderInput,
  theme: ReturnType<typeof getCertificateTheme>,
) {
  const { dark, darkSoft, accent, accentLight, paper, ink, muted, accentText } = theme;
  context.fillStyle = paper;
  context.fillRect(0, 0, WIDTH, HEIGHT);

  context.fillStyle = `${dark}0a`;
  let seed = Number.parseInt(input.recordHash.slice(0, 8), 16) || 1;
  for (let index = 0; index < 280; index += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const x = seed % WIDTH;
    seed = (seed * 1664525 + 1013904223) >>> 0;
    context.fillRect(x, seed % HEIGHT, 2, 2);
  }

  context.strokeStyle = dark;
  context.lineWidth = 18;
  roundedRect(context, 34, 34, WIDTH - 68, HEIGHT - 68, 42);
  context.stroke();
  context.strokeStyle = accent;
  context.lineWidth = 4;
  roundedRect(context, 56, 56, WIDTH - 112, HEIGHT - 112, 28);
  context.stroke();

  context.fillStyle = dark;
  roundedRect(context, 88, 54, 1340, 166, 18);
  context.fill();
  context.fillStyle = accent;
  context.fillRect(88, 194, 1340, 26);

  let logo: HTMLImageElement | undefined;
  if (input.logo) {
    try {
      logo = await fileToImage(input.logo);
    } catch {
      logo = undefined;
    }
  }
  let logoTextX = 255;
  if (logo) {
    const fittedLogo = fitImageWithin(logo.naturalWidth, logo.naturalHeight, 300, 112);
    const logoFrame = {
      x: 108,
      y: 66 + (128 - fittedLogo.height - 16) / 2,
      width: fittedLogo.width + 16,
      height: fittedLogo.height + 16,
    };
    context.fillStyle = `${paper}f2`;
    roundedRect(context, logoFrame.x, logoFrame.y, logoFrame.width, logoFrame.height, 8);
    context.fill();
    context.strokeStyle = accentLight;
    context.lineWidth = 2;
    context.stroke();
    context.drawImage(logo, logoFrame.x + 8, logoFrame.y + 8, fittedLogo.width, fittedLogo.height);
    logoTextX = Math.max(255, logoFrame.x + logoFrame.width + 24);
  } else {
    context.strokeStyle = accentLight;
    context.lineWidth = 5;
    roundedRect(context, 122, 92, 82, 62, 6);
    context.stroke();
    context.fillStyle = accentLight;
    context.font = "800 34px Arial, sans-serif";
    context.textAlign = "center";
    context.fillText("M", 163, 135);
    context.textAlign = "left";
  }

  context.fillStyle = accentLight;
  const collectionMaximumWidth = Math.min(1050, 1404 - logoTextX);
  const collectionSize = fitFontSize(
    context,
    input.values.collectionName.toUpperCase(),
    collectionMaximumWidth,
    28,
    "Arial, sans-serif",
    "800",
  );
  context.font = `800 ${collectionSize}px Arial, sans-serif`;
  context.fillText(fitTextWithEllipsis(context, input.values.collectionName.toUpperCase(), collectionMaximumWidth), logoTextX, 134);
  context.fillStyle = paper;
  context.font = "800 18px Arial, sans-serif";
  context.fillText("SCIENTIFIC SPECIMEN IDENTIFICATION CARD", logoTextX, 169);

  context.fillStyle = `${accent}1c`;
  roundedRect(context, 1470, 68, 642, 132, 18);
  context.fill();
  context.strokeStyle = dark;
  context.lineWidth = 4;
  roundedRect(context, 1470, 68, 642, 132, 18);
  context.stroke();
  context.fillStyle = accentText;
  context.font = "800 18px Arial, sans-serif";
  context.fillText("SPECIMEN RECORD", 1502, 108);
  context.fillStyle = dark;
  const idSize = fitFontSize(context, input.values.certificateId, 570, 34, "Arial, sans-serif", "800");
  context.font = `800 ${idSize}px Arial, sans-serif`;
  context.fillText(fitTextWithEllipsis(context, input.values.certificateId, 570), 1502, 154);
  context.fillStyle = input.values.certificateStatus === "active" ? accentText : "#8b2a2a";
  context.font = "800 18px Arial, sans-serif";
  context.fillText(input.values.certificateStatus.toUpperCase(), 1502, 184);

  context.fillStyle = accentText;
  context.font = "800 20px Arial, sans-serif";
  context.fillText("CERTIFICATE OF AUTHENTICITY / METEORITE SPECIMEN", 112, 248);
  context.fillStyle = dark;
  const nameSize = fitFontSize(context, input.values.meteoriteName, 1570, 92, 'Impact, "Arial Narrow", Arial, sans-serif', "800");
  context.font = `800 ${nameSize}px Impact, "Arial Narrow", Arial, sans-serif`;
  context.fillText(fitTextWithEllipsis(context, input.values.meteoriteName, 1570), 110, 322);
  context.fillStyle = accentText;
  context.font = "800 30px Arial, sans-serif";
  context.fillText(fitTextWithEllipsis(context, classificationSummary(input.values).toUpperCase(), 1570), 112, 378);
  context.fillStyle = dark;
  context.fillRect(110, 408, 1980, 8);
  context.fillStyle = accent;
  context.fillRect(110, 420, 1980, 4);

  const photoFrame = {
    x: 110,
    y: 466,
    width: Math.round(578 * CELESTIAL_PHOTO_ASPECT_RATIO),
    height: 578,
  };
  context.fillStyle = `${paper}ee`;
  roundedRect(context, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height, 18);
  context.fill();
  context.strokeStyle = dark;
  context.lineWidth = 7;
  roundedRect(context, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height, 18);
  context.stroke();
  context.strokeStyle = accent;
  context.lineWidth = 3;
  roundedRect(context, photoFrame.x + 14, photoFrame.y + 14, photoFrame.width - 28, photoFrame.height - 72, 10);
  context.stroke();
  const photo = await fileToImage(input.mainPhoto);
  const photoWidth = 560;
  const photoHeight = 455;
  drawContainedImage(
    context,
    photo,
    photoFrame.x + (photoFrame.width - photoWidth) / 2,
    photoFrame.y + 25,
    photoWidth,
    photoHeight,
  );
  context.fillStyle = dark;
  context.fillRect(photoFrame.x + 3, photoFrame.y + photoFrame.height - 55, photoFrame.width - 6, 52);
  context.fillStyle = accentLight;
  context.font = "800 18px ui-monospace, SFMono-Regular, Menlo, monospace";
  context.fillText("CONTAINED SOURCE PHOTO 01", photoFrame.x + 26, photoFrame.y + photoFrame.height - 21);

  const panelX = photoFrame.x + photoFrame.width + 40;
  const panelY = 466;
  const panelWidth = 2090 - panelX;
  const panelHeight = 578;
  context.fillStyle = `${paper}ee`;
  roundedRect(context, panelX, panelY, panelWidth, panelHeight, 18);
  context.fill();
  context.strokeStyle = dark;
  context.lineWidth = 5;
  roundedRect(context, panelX, panelY, panelWidth, panelHeight, 18);
  context.stroke();
  context.fillStyle = dark;
  roundedRect(context, panelX, panelY, panelWidth, 66, 18);
  context.fill();
  context.fillRect(panelX, panelY + 32, panelWidth, 34);
  context.fillStyle = paper;
  context.font = "800 22px Arial, sans-serif";
  context.fillText("CATALOG FACTS", panelX + 28, panelY + 43);

  const catalogRows = [
    ["FALL / FIND", input.values.fallStatus],
    ["DATE", displayDate(input.values.fallDate)],
    ["LOCATION", formatCertificateLocation(input.values)],
    ["COORDINATES", recordedCoordinates(input.values.latitude, input.values.longitude)],
    ["SPECIMEN FORM", input.values.specimenForm],
    ["CURRENT OWNER", recorded(input.values.issuerName)],
    ["ISSUED", displayDate(input.values.issueDate)],
  ];
  const rowHeight = (panelHeight - 66) / catalogRows.length;
  catalogRows.forEach(([label, value], index) => {
    const rowY = panelY + 66 + index * rowHeight;
    if (index % 2 === 1) {
      context.fillStyle = `${accent}14`;
      context.fillRect(panelX + 3, rowY, panelWidth - 6, rowHeight);
    }
    context.strokeStyle = `${dark}4d`;
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(panelX + 3, rowY + rowHeight);
    context.lineTo(panelX + panelWidth - 3, rowY + rowHeight);
    context.stroke();
    context.fillStyle = accentText;
    context.font = "800 18px Arial, sans-serif";
    context.fillText(label, panelX + 28, rowY + 45);
    context.fillStyle = ink;
    const valueWidth = panelWidth - 360;
    const valueSize = fitFontSize(context, value, valueWidth, 24, "Arial, sans-serif", "700");
    context.font = `700 ${valueSize}px Arial, sans-serif`;
    context.fillText(fitTextWithEllipsis(context, value, valueWidth), panelX + 320, rowY + 45);
  });

  const measurementY = 1082;
  context.fillStyle = `${accent}16`;
  context.fillRect(110, measurementY, 1980, 120);
  context.strokeStyle = dark;
  context.lineWidth = 5;
  context.strokeRect(110, measurementY, 1980, 120);
  const measurements = [
    ["WEIGHT", `${input.values.weightGrams} g`],
    ["DIMENSIONS", recorded(input.values.dimensions)],
    ["PIECES", input.values.numberOfPieces],
    ["PREPARATION", recorded(input.values.preparationState)],
  ];
  measurements.forEach(([label, value], index) => {
    const x = 110 + index * 495;
    if (index > 0) {
      context.strokeStyle = `${dark}80`;
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(x, measurementY);
      context.lineTo(x, measurementY + 120);
      context.stroke();
    }
    context.fillStyle = accentText;
    context.font = "800 18px Arial, sans-serif";
    context.fillText(label, x + 28, measurementY + 37);
    context.fillStyle = dark;
    const valueSize = fitFontSize(context, value, 430, 36, 'Impact, "Arial Narrow", Arial, sans-serif', "800");
    context.font = `800 ${valueSize}px Impact, "Arial Narrow", Arial, sans-serif`;
    context.fillText(fitTextWithEllipsis(context, value, 430), x + 28, measurementY + 85);
  });

  const notesX = 110;
  const notesY = 1240;
  const notesWidth = 970;
  const notesHeight = 280;
  context.fillStyle = `${paper}f2`;
  context.fillRect(notesX, notesY, notesWidth, notesHeight);
  context.strokeStyle = dark;
  context.lineWidth = 4;
  context.strokeRect(notesX, notesY, notesWidth, notesHeight);
  context.fillStyle = dark;
  context.fillRect(notesX, notesY, notesWidth, 54);
  context.fillStyle = paper;
  context.font = "800 20px Arial, sans-serif";
  context.fillText("PROVENANCE / CATALOG NOTES", notesX + 24, notesY + 36);
  context.fillStyle = ink;
  context.font = "400 21px Arial, sans-serif";
  wrapText(context, recorded(input.values.provenance), notesX + 24, notesY + 92, notesWidth - 48, 29, 4);
  const supplemental = input.values.recoveryInformation.trim()
    ? `Recovery: ${input.values.recoveryInformation.trim()}`
    : input.values.identifyingMarks.trim()
      ? `Identifying marks: ${input.values.identifyingMarks.trim()}`
      : "Not recorded";
  context.fillStyle = muted;
  context.font = "400 18px Arial, sans-serif";
  wrapText(context, supplemental, notesX + 24, notesY + 232, notesWidth - 48, 24, 2);

  const authX = 1120;
  const authY = 1240;
  const authWidth = 970;
  const authHeight = 280;
  context.fillStyle = dark;
  roundedRect(context, authX, authY, authWidth, authHeight, 18);
  context.fill();
  context.strokeStyle = accent;
  context.lineWidth = 5;
  roundedRect(context, authX, authY, authWidth, authHeight, 18);
  context.stroke();
  context.fillStyle = accentLight;
  context.font = "800 20px Arial, sans-serif";
  context.fillText("AUTHENTICATION / RECORD VERIFICATION", authX + 30, authY + 42);
  context.fillStyle = paper;
  context.font = "800 30px Arial, sans-serif";
  context.fillText(fitTextWithEllipsis(context, input.values.issuerName, 580), authX + 30, authY + 93);
  context.fillStyle = accentLight;
  context.font = "800 17px Arial, sans-serif";
  context.fillText("ED25519 DIGITAL SIGNATURE", authX + 30, authY + 132);
  context.fillStyle = paper;
  context.font = "400 17px ui-monospace, SFMono-Regular, Menlo, monospace";
  context.fillText(fitTextWithEllipsis(context, input.fingerprint, 590), authX + 30, authY + 168);
  context.fillStyle = `${paper}b8`;
  context.font = "400 18px Arial, sans-serif";
  context.fillText(`Certificate version ${input.values.certificateVersion}`, authX + 30, authY + 213);
  context.fillText("Scan to inspect the signed record payload.", authX + 30, authY + 244);

  const qrDataUrl = await QRCode.toDataURL(input.qrPayload, {
    errorCorrectionLevel: "M",
    margin: 2,
    width: 224,
    color: { dark, light: "#ffffff" },
  });
  const qr = await dataUrlToImage(qrDataUrl);
  context.fillStyle = "#ffffff";
  roundedRect(context, authX + authWidth - 244, authY + 18, 224, 244, 10);
  context.fill();
  context.drawImage(qr, authX + authWidth - 234, authY + 28, 204, 204);
  context.fillStyle = dark;
  context.font = "800 16px Arial, sans-serif";
  context.textAlign = "center";
  context.fillText("SHA-256", authX + authWidth - 132, authY + 250);
  context.textAlign = "left";

  context.fillStyle = muted;
  context.font = `400 ${certificateFooterLayout.fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillText(`Record SHA-256: ${input.recordHash}`, 112, certificateFooterLayout.recordHashBaseline);
  context.fillText(`Key FP: ${input.fingerprint}`, 112, certificateFooterLayout.keyFingerprintBaseline);
}

async function encodeCertificateCanvas(canvas: HTMLCanvasElement, input: CertificateRenderInput) {
  const pngBlob = await canvasToBlob(canvas);
  const png = new Uint8Array(await pngBlob.arrayBuffer());
  const pdfDocument = new jsPDF({
    orientation: "landscape",
    unit: "in",
    format: "letter",
    compress: true,
  });
  pdfDocument.setProperties({
    title: `${input.values.certificateId} - Certificate of Authenticity`,
    subject: `${input.values.meteoriteName} meteorite specimen`,
    author: input.values.issuerName,
    creator: "Spacerocks COA Studio",
  });
  pdfDocument.addImage(png, "PNG", 0, 0, 11, 8.5, undefined, "FAST");
  const pdf = new Uint8Array(pdfDocument.output("arraybuffer"));
  return { png, pdf };
}

export async function renderCertificate(input: CertificateRenderInput): Promise<{
  png: Uint8Array;
  pdf: Uint8Array;
}> {
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser cannot create a certificate canvas.");

  const theme = getCertificateTheme(input.values.certificateTheme);
  const certificateStyle = input.values.certificateStyle;
  const NAVY = theme.dark;
  const NAVY_SOFT = theme.darkSoft;
  const GOLD = theme.accent;
  const GOLD_LIGHT = theme.accentLight;
  const IVORY = theme.paper;
  const INK = theme.ink;
  const MUTED = theme.muted;

  if (certificateStyle === "museum-type") {
    await drawMuseumTypeCertificate(context, input, theme);
    return encodeCertificateCanvas(canvas, input);
  }

  context.fillStyle = IVORY;
  context.fillRect(0, 0, WIDTH, HEIGHT);

  // A subtle deterministic paper texture avoids a sterile digital appearance.
  let seed = Number.parseInt(input.recordHash.slice(0, 8), 16) || 1;
  context.fillStyle = `${INK}0b`;
  const textureMarks = certificateStyle === "museum-ledger" ? 260 : certificateStyle === "celestial-formal" ? 900 : 1800;
  for (let index = 0; index < textureMarks; index += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const x = seed % WIDTH;
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const y = seed % HEIGHT;
    context.fillRect(x, y, 1 + (seed % 3), 1 + ((seed >>> 2) % 2));
  }

  drawStyleFoundation(context, certificateStyle, NAVY, GOLD);

  if (certificateStyle === "museum-ledger") {
    context.fillStyle = IVORY;
    context.fillRect(0, 0, WIDTH, 372);
    context.fillStyle = NAVY;
    context.fillRect(0, 0, WIDTH, 24);
    context.fillRect(1364, 48, 788, 302);
    context.fillStyle = GOLD;
    context.fillRect(72, 48, 10, 302);
    context.fillRect(1351, 48, 5, 302);
  } else if (certificateStyle === "celestial-formal") {
    const headerGradient = context.createLinearGradient(0, 0, WIDTH, 372);
    headerGradient.addColorStop(0, NAVY);
    headerGradient.addColorStop(1, NAVY_SOFT);
    context.fillStyle = headerGradient;
    context.fillRect(0, 0, WIDTH, 372);
    context.fillStyle = GOLD_LIGHT;
    for (const [x, y, radius] of [[420, 74, 3], [1080, 225, 2], [1520, 92, 4], [1880, 286, 2]] as const) {
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
    }
  } else {
    const headerGradient = context.createLinearGradient(0, 0, WIDTH, 372);
    headerGradient.addColorStop(0, IVORY);
    headerGradient.addColorStop(0.5, "#ffffff");
    headerGradient.addColorStop(1, IVORY);
    context.fillStyle = headerGradient;
    context.fillRect(0, 0, WIDTH, 372);
    context.fillStyle = NAVY;
    context.fillRect(0, 0, WIDTH, 22);
    context.strokeStyle = GOLD;
    context.lineWidth = 3;
    context.strokeRect(72, 42, WIDTH - 144, 286);
    context.strokeStyle = `${NAVY}99`;
    context.lineWidth = 2;
    context.strokeRect(86, 56, WIDTH - 172, 258);
    context.strokeStyle = GOLD;
    context.beginPath();
    context.moveTo(650, 348);
    context.lineTo(790, 348);
    context.moveTo(850, 348);
    context.lineTo(990, 348);
    context.stroke();
    context.save();
    context.translate(820, 348);
    context.rotate(Math.PI / 4);
    context.strokeRect(-11, -11, 22, 22);
    context.restore();
  }

  drawStyleFrame(context, certificateStyle, NAVY, GOLD);

  let logo: HTMLImageElement | undefined;
  if (input.logo) {
    try {
      logo = await fileToImage(input.logo);
    } catch {
      logo = undefined;
    }
  }

  let headerContentX = 255;
  let headerTitleX = 105;
  if (logo) {
    if (certificateStyle === "museum-ledger") {
      context.fillStyle = "#ffffff99";
      context.fillRect(100, 61, 132, 122);
      context.strokeStyle = GOLD;
      context.lineWidth = 3;
      context.strokeRect(100, 61, 132, 122);
      drawContainedImage(context, logo, 105, 70, 120, 105);
    } else if (certificateStyle === "celestial-formal") {
      const fittedLogo = fitImageWithin(logo.naturalWidth, logo.naturalHeight, 320, 184);
      const logoFrame = {
        x: 95,
        y: 44 + (204 - fittedLogo.height - 20) / 2,
        width: fittedLogo.width + 20,
        height: fittedLogo.height + 20,
      };
      context.fillStyle = `${NAVY}66`;
      roundedRect(context, logoFrame.x, logoFrame.y, logoFrame.width, logoFrame.height, 12);
      context.fill();
      context.strokeStyle = `${GOLD_LIGHT}99`;
      context.lineWidth = 2;
      roundedRect(context, logoFrame.x, logoFrame.y, logoFrame.width, logoFrame.height, 12);
      context.stroke();
      context.drawImage(logo, logoFrame.x + 10, logoFrame.y + 10, fittedLogo.width, fittedLogo.height);
      headerContentX = logoFrame.x + logoFrame.width + 24;
      headerTitleX = headerContentX;
    } else {
      drawContainedImage(context, logo, 105, 70, 120, 105);
    }
  } else if (certificateStyle === "museum-ledger") drawLedgerMark(context, 162, 122, NAVY);
  else drawOrbitMark(context, 162, 122, certificateStyle === "regal-archive" ? NAVY : GOLD_LIGHT);

  const headerAccent = certificateStyle === "celestial-formal" ? GOLD_LIGHT : theme.accentText;
  const headerInk = certificateStyle === "celestial-formal" ? "#fbf8ef" : NAVY;
  context.fillStyle = headerAccent;
  const collectionFontSize = fitFontSize(
    context,
    input.values.collectionName.toUpperCase(),
    certificateStyle === "museum-ledger" ? 1050 : certificateStyle === "celestial-formal" && logo ? 1420 - headerContentX : 1500,
    28,
    "Arial, sans-serif",
    "600",
  );
  context.font = `600 ${collectionFontSize}px Arial, sans-serif`;
  const collectionMaximumWidth = certificateStyle === "museum-ledger" ? 1050 : certificateStyle === "celestial-formal" && logo ? 1420 - headerContentX : 1500;
  context.fillText(fitTextWithEllipsis(context, input.values.collectionName.toUpperCase(), collectionMaximumWidth), headerContentX, 134);

  context.fillStyle = headerAccent;
  context.font = "700 18px Arial, sans-serif";
  context.fillText(certificateStyle === "museum-ledger" ? "SIGNED SPECIMEN CATALOG" : "ARCHIVAL SPECIMEN RECORD", headerTitleX, 211);

  context.fillStyle = headerInk;
  const certificateTitle = "CERTIFICATE OF AUTHENTICITY";
  const certificateTitleSize = fitFontSize(
    context,
    certificateTitle,
    certificateStyle === "museum-ledger" ? 1200 : certificateStyle === "celestial-formal" && logo ? 1420 - headerTitleX : 1450,
    72,
    "Georgia, serif",
  );
  context.font = `400 ${certificateTitleSize}px Georgia, serif`;
  if (certificateStyle === "regal-archive") {
    context.textAlign = "center";
    context.fillText(certificateTitle, 820, 286);
    context.textAlign = "left";
  } else context.fillText(certificateTitle, headerTitleX, 286);

  context.textAlign = "right";
  context.fillStyle = certificateStyle === "museum-ledger" ? GOLD_LIGHT : headerAccent;
  context.font = "600 21px Arial, sans-serif";
  context.fillText(certificateStyle === "museum-ledger" ? "COA CATALOG ID" : "CERTIFICATE ID", 2080, 105);
  context.fillStyle = certificateStyle === "museum-ledger" ? IVORY : headerInk;
  context.font = "600 36px Georgia, serif";
  context.fillText(input.values.certificateId, 2080, 166);
  const statusText = input.values.certificateStatus.toUpperCase();
  context.font = "700 19px Arial, sans-serif";
  if (input.values.certificateStatus === "active") {
    context.fillStyle = certificateStyle === "museum-ledger" ? GOLD_LIGHT : headerAccent;
    context.fillText(statusText, 2080, 213);
  } else {
    const statusWidth = context.measureText(statusText).width + 38;
    context.fillStyle = "#8b2a2a";
    context.fillRect(2080 - statusWidth, 184, statusWidth, 42);
    context.strokeStyle = GOLD_LIGHT;
    context.lineWidth = 2;
    context.strokeRect(2080 - statusWidth, 184, statusWidth, 42);
    context.fillStyle = "#ffffff";
    context.fillText(statusText, 2061, 212);
  }
  context.textAlign = "left";

  const titleSize = fitFontSize(context, input.values.meteoriteName, 1260, 98, "Georgia, serif");
  context.font = `400 ${titleSize}px Georgia, serif`;
  context.fillStyle = NAVY;
  context.fillText(fitTextWithEllipsis(context, input.values.meteoriteName, 1260), 112, 522);
  context.fillStyle = theme.accentText;
  context.font = "600 31px Arial, sans-serif";
  context.fillText(fitTextWithEllipsis(context, classificationSummary(input.values).toUpperCase(), 1304), 116, 590);
  context.strokeStyle = certificateStyle === "museum-ledger" ? NAVY : GOLD;
  context.lineWidth = certificateStyle === "museum-ledger" ? 4 : certificateStyle === "regal-archive" ? 7 : 3;
  context.beginPath();
  context.moveTo(116, 625);
  context.lineTo(1420, 625);
  context.stroke();
  if (certificateStyle === "regal-archive") {
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(116, 637);
    context.lineTo(1420, 637);
    context.stroke();
  }

  const photoFrame = { x: 1510, y: 426, width: 560, height: 455 };
  const detailRadius = certificateStyle === "museum-ledger" ? 0 : certificateStyle === "celestial-formal" ? 28 : 8;
  context.fillStyle = certificateStyle === "museum-ledger" ? NAVY : certificateStyle === "celestial-formal" ? GOLD : NAVY;
  roundedRect(context, photoFrame.x - 10, photoFrame.y - 10, photoFrame.width + 20, photoFrame.height + 20, detailRadius);
  context.fill();
  if (certificateStyle === "museum-ledger") {
    context.strokeStyle = GOLD;
    context.lineWidth = 4;
    context.stroke();
  } else if (certificateStyle === "regal-archive") {
    context.strokeStyle = GOLD;
    context.lineWidth = 5;
    context.stroke();
  }
  context.save();
  roundedRect(context, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height, Math.max(0, detailRadius - 8));
  context.clip();
  const photo = await fileToImage(input.mainPhoto);
  drawContainedImage(context, photo, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height);
  context.restore();
  if (certificateStyle === "museum-ledger") {
    context.fillStyle = NAVY;
    context.fillRect(photoFrame.x, photoFrame.y + photoFrame.height - 54, photoFrame.width, 54);
    context.fillStyle = GOLD_LIGHT;
    context.font = `700 ${CERTIFICATE_EXPORT_FONT_FLOOR}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    context.fillText("PHOTO RECORD 01", photoFrame.x + 22, photoFrame.y + photoFrame.height - 20);
  }
  context.fillStyle = NAVY;
  context.font = "600 18px Arial, sans-serif";
  context.fillText("COMPLETE SOURCE PHOTO / CENTERED CONTAIN FIT", photoFrame.x, photoFrame.y + photoFrame.height + 42);

  const rows = [
    ["METEORITE", input.values.meteoriteName],
    ["FALL / FIND", input.values.fallStatus],
    ["DATE", displayDate(input.values.fallDate)],
    ["LOCATION", formatCertificateLocation(input.values)],
    ["COORDINATES", recordedCoordinates(input.values.latitude, input.values.longitude)],
    ["SPECIMEN FORM", input.values.specimenForm],
    ["CURRENT OWNER", recorded(input.values.issuerName)],
  ];
  const tableX = 112;
  const tableY = 680;
  const tableWidth = 1308;
  const rowHeight = 82;
  const labelColumnWidth = certificateStyle === "museum-ledger" ? 345 : 370;
  if (certificateStyle === "museum-ledger") {
    context.fillStyle = "#ffffff99";
    context.fillRect(tableX, tableY, tableWidth, rows.length * rowHeight);
    context.fillStyle = NAVY;
    context.font = "700 17px Arial, sans-serif";
    context.fillText("OBJECT CATALOG / SIGNED SPECIMEN RECORD", tableX, tableY - 25);
  } else if (certificateStyle === "regal-archive") {
    context.fillStyle = "#ffffff80";
    context.fillRect(tableX, tableY, tableWidth, rows.length * rowHeight);
  } else if (certificateStyle === "celestial-formal") {
    context.fillStyle = `${NAVY}0d`;
    roundedRect(context, tableX, tableY, tableWidth, rows.length * rowHeight, 22);
    context.fill();
  }
  context.strokeStyle = certificateStyle === "museum-ledger" ? NAVY : `${GOLD}a6`;
  context.lineWidth = certificateStyle === "museum-ledger" ? 2 : 3;
  roundedRect(context, tableX, tableY, tableWidth, rows.length * rowHeight, certificateStyle === "celestial-formal" ? 18 : 0);
  context.stroke();
  if (certificateStyle === "regal-archive") {
    context.strokeStyle = `${NAVY}99`;
    context.lineWidth = 2;
    context.strokeRect(tableX + 12, tableY + 12, tableWidth - 24, rows.length * rowHeight - 24);
  }
  context.beginPath();
  context.moveTo(tableX + labelColumnWidth, tableY);
  context.lineTo(tableX + labelColumnWidth, tableY + rows.length * rowHeight);
  context.stroke();

  rows.forEach(([label, value], index) => {
    const y = tableY + index * rowHeight;
    if (certificateStyle === "museum-ledger") {
      context.fillStyle = NAVY;
      context.fillRect(tableX + 2, y + 2, labelColumnWidth - 2, rowHeight - 4);
      context.strokeStyle = `${NAVY}4d`;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(tableX + labelColumnWidth, y + rowHeight);
      context.lineTo(tableX + tableWidth, y + rowHeight);
      context.stroke();
    } else if (certificateStyle === "regal-archive") {
      context.fillStyle = `${GOLD}1f`;
      context.fillRect(tableX + 14, y + 14, labelColumnWidth - 27, rowHeight - 14);
      context.strokeStyle = `${NAVY}33`;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(tableX + 12, y + rowHeight);
      context.lineTo(tableX + tableWidth - 12, y + rowHeight);
      context.stroke();
    } else if (index % 2 === 1) {
      context.fillStyle = `${GOLD}17`;
      context.fillRect(tableX + 2, y, tableWidth - 4, rowHeight);
    }
    context.fillStyle = certificateStyle === "museum-ledger" ? GOLD_LIGHT : NAVY;
    context.font = "700 21px Arial, sans-serif";
    context.fillText(label, tableX + 32, y + 50);
    context.fillStyle = INK;
    context.font = "400 26px Georgia, serif";
    const displayed = value.length > 55 ? `${value.slice(0, 52)}...` : value;
    context.fillText(displayed, tableX + labelColumnWidth + 40, y + 51);
  });

  roundedRect(context, 1510, 960, 560, 212, detailRadius);
  if (certificateStyle === "museum-ledger") {
    context.fillStyle = NAVY;
    context.fill();
    context.strokeStyle = GOLD;
    context.lineWidth = 4;
    context.stroke();
    context.strokeStyle = `${GOLD_LIGHT}66`;
    context.lineWidth = 2;
    context.strokeRect(1525, 975, 530, 182);
    context.fillStyle = GOLD;
    context.fillRect(1510, 960, 16, 212);
  } else if (certificateStyle === "celestial-formal") {
    const detailGradient = context.createLinearGradient(1510, 960, 2070, 1172);
    detailGradient.addColorStop(0, NAVY);
    detailGradient.addColorStop(1, NAVY_SOFT);
    context.fillStyle = detailGradient;
    context.fill();
    context.strokeStyle = GOLD;
    context.lineWidth = 3;
    context.stroke();
  } else {
    context.fillStyle = "#ffffffcc";
    context.fill();
    context.strokeStyle = GOLD;
    context.lineWidth = 4;
    context.stroke();
    context.strokeStyle = NAVY;
    context.lineWidth = 2;
    context.strokeRect(1526, 976, 528, 180);
    context.strokeStyle = GOLD;
    context.beginPath();
    context.moveTo(1680, 1147);
    context.lineTo(1755, 1147);
    context.moveTo(1825, 1147);
    context.lineTo(1900, 1147);
    context.stroke();
  }
  context.fillStyle = certificateStyle === "regal-archive" ? theme.accentText : GOLD_LIGHT;
  context.textAlign = "center";
  context.font = "600 19px Arial, sans-serif";
  context.fillText("SPECIMEN DETAILS", 1790, 1018);
  context.fillStyle = certificateStyle === "regal-archive" ? NAVY : "#ffffff";
  context.font = "400 59px Georgia, serif";
  context.fillText(`${input.values.weightGrams} g`, 1790, 1092);
  context.fillStyle = certificateStyle === "regal-archive" ? theme.accentText : GOLD_LIGHT;
  context.font = "600 18px Arial, sans-serif";
  context.fillText(input.values.specimenForm.toUpperCase(), 1790, 1132);
  context.textAlign = "left";

  const qrDataUrl = await QRCode.toDataURL(input.qrPayload, {
    errorCorrectionLevel: "M",
    margin: 2,
    width: 310,
    color: { dark: NAVY, light: "#ffffff" },
  });
  const qr = await dataUrlToImage(qrDataUrl);
  context.fillStyle = NAVY;
  context.font = "700 18px Arial, sans-serif";
  context.fillText("RECORD VERIFICATION", 1734, 1202);
  roundedRect(context, 1734, 1226, 336, 336, certificateStyle === "museum-ledger" ? 0 : 18);
  context.fillStyle = "#ffffff";
  context.fill();
  context.strokeStyle = GOLD;
  context.lineWidth = 3;
  context.stroke();
  context.drawImage(qr, 1747, 1239, 310, 310);

  if (certificateStyle === "museum-ledger") {
    context.save();
    context.translate(1595, 1394);
    context.rotate(-0.04);
    context.fillStyle = `${IVORY}dd`;
    context.beginPath();
    context.arc(0, 0, 91, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = NAVY;
    context.lineWidth = 4;
    context.stroke();
    context.beginPath();
    context.arc(0, 0, 76, 0, Math.PI * 2);
    context.stroke();
    context.fillStyle = NAVY;
    context.textAlign = "center";
    context.font = "700 20px Arial, sans-serif";
    context.fillText("SHA", 0, -8);
    context.fillStyle = theme.accentText;
    context.font = "700 16px Arial, sans-serif";
    context.fillText("256", 0, 25);
    context.strokeStyle = GOLD;
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(-42, -38);
    context.lineTo(42, -38);
    context.moveTo(-42, 42);
    context.lineTo(42, 42);
    context.stroke();
    context.restore();
  } else if (certificateStyle === "regal-archive") {
    context.save();
    context.translate(1595, 1394);
    context.fillStyle = `${IVORY}ee`;
    context.beginPath();
    context.arc(0, 0, 94, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = GOLD;
    context.lineWidth = 5;
    context.stroke();
    context.beginPath();
    context.arc(0, 0, 79, 0, Math.PI * 2);
    context.strokeStyle = NAVY;
    context.lineWidth = 2;
    context.stroke();
    context.save();
    context.rotate(Math.PI / 4);
    context.strokeStyle = GOLD;
    context.strokeRect(-42, -42, 84, 84);
    context.restore();
    context.fillStyle = NAVY;
    context.textAlign = "center";
    context.font = "700 20px Arial, sans-serif";
    context.fillText("SHA", 0, -8);
    context.fillStyle = theme.accentText;
    context.font = "700 16px Arial, sans-serif";
    context.fillText("256", 0, 25);
    context.restore();
  }

  if (certificateStyle === "regal-archive") {
    context.strokeStyle = `${GOLD}b3`;
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(112, 1302);
    context.lineTo(640, 1302);
    context.moveTo(730, 1302);
    context.lineTo(1210, 1302);
    context.stroke();
  } else if (certificateStyle === "museum-ledger") {
    context.strokeStyle = NAVY;
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(112, 1302);
    context.lineTo(1210, 1302);
    context.stroke();
  }

  context.fillStyle = NAVY;
  context.font = "600 18px Arial, sans-serif";
  context.fillText("DIGITALLY SIGNED BY", 112, 1335);
  context.font = "400 35px Georgia, serif";
  context.fillText(input.values.issuerName, 112, 1390);
  context.fillStyle = MUTED;
  context.font = "400 19px Arial, sans-serif";
  context.fillText(input.values.collectionName, 112, 1428);

  context.fillStyle = NAVY;
  context.font = "600 18px Arial, sans-serif";
  context.fillText("ISSUED", 730, 1335);
  context.font = "400 31px Georgia, serif";
  context.fillText(displayDate(input.values.issueDate), 730, 1390);
  context.fillStyle = MUTED;
  context.font = "400 19px Arial, sans-serif";
  context.fillText(`Version ${input.values.certificateVersion}`, 730, 1428);

  context.fillStyle = NAVY;
  context.font = "600 18px Arial, sans-serif";
  context.fillText("PROVENANCE", 112, 1495);
  context.fillStyle = INK;
  context.font = "400 19px Arial, sans-serif";
  wrapText(context, recorded(input.values.provenance), 112, 1534, 1490, 27, 2);

  context.fillStyle = MUTED;
  context.font = `400 ${certificateFooterLayout.fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillText(`Record SHA-256: ${input.recordHash}`, 112, certificateFooterLayout.recordHashBaseline);
  context.fillText(`Key FP: ${input.fingerprint}`, 112, certificateFooterLayout.keyFingerprintBaseline);

  return encodeCertificateCanvas(canvas, input);
}
