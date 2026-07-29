import { jsPDF } from "jspdf";
import QRCode from "qrcode";
import type { FormValues } from "../types";
import { certificateFooterLayout, getCertificateTheme } from "../certificateThemes";
import { displayDate } from "./core";

const WIDTH = 2200;
const HEIGHT = 1700;

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
  while (size > 38) {
    context.font = `${weight} ${size}px ${family}`;
    if (context.measureText(text).width <= maximumWidth) return size;
    size -= 2;
  }
  return size;
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
  const words = text.trim().split(/\s+/);
  const lines: string[] = [];
  let line = "";

  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (context.measureText(candidate).width > maxWidth && line) {
      lines.push(line);
      line = word;
    } else {
      line = candidate;
    }
  }
  if (line) lines.push(line);

  const visible = lines.slice(0, maxLines);
  if (lines.length > maxLines) {
    let last = visible[maxLines - 1] ?? "";
    while (context.measureText(`${last}...`).width > maxWidth && last.includes(" ")) {
      last = last.slice(0, last.lastIndexOf(" "));
    }
    visible[maxLines - 1] = `${last}...`;
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

function drawCoverImage(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  x: number,
  y: number,
  width: number,
  height: number,
) {
  const imageRatio = image.naturalWidth / image.naturalHeight;
  const frameRatio = width / height;
  let sourceX = 0;
  let sourceY = 0;
  let sourceWidth = image.naturalWidth;
  let sourceHeight = image.naturalHeight;

  if (imageRatio > frameRatio) {
    sourceWidth = image.naturalHeight * frameRatio;
    sourceX = (image.naturalWidth - sourceWidth) / 2;
  } else {
    sourceHeight = image.naturalWidth / frameRatio;
    sourceY = (image.naturalHeight - sourceHeight) / 2;
  }

  context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, x, y, width, height);
}

function drawContainedImage(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  x: number,
  y: number,
  width: number,
  height: number,
) {
  const scale = Math.min(width / image.naturalWidth, height / image.naturalHeight);
  const drawWidth = image.naturalWidth * scale;
  const drawHeight = image.naturalHeight * scale;
  context.drawImage(image, x + (width - drawWidth) / 2, y + (height - drawHeight) / 2, drawWidth, drawHeight);
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
  const NAVY = theme.dark;
  const NAVY_SOFT = theme.darkSoft;
  const GOLD = theme.accent;
  const GOLD_LIGHT = theme.accentLight;
  const IVORY = theme.paper;
  const INK = theme.ink;
  const MUTED = theme.muted;

  context.fillStyle = IVORY;
  context.fillRect(0, 0, WIDTH, HEIGHT);

  // A subtle deterministic paper texture avoids a sterile digital appearance.
  let seed = Number.parseInt(input.recordHash.slice(0, 8), 16) || 1;
  context.fillStyle = `${INK}0b`;
  for (let index = 0; index < 1800; index += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const x = seed % WIDTH;
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const y = seed % HEIGHT;
    context.fillRect(x, y, 1 + (seed % 3), 1 + ((seed >>> 2) % 2));
  }

  context.fillStyle = NAVY;
  context.fillRect(0, 0, WIDTH, 372);

  context.strokeStyle = GOLD;
  context.lineWidth = 8;
  context.strokeRect(25, 25, WIDTH - 50, HEIGHT - 50);
  context.lineWidth = 2;
  context.strokeRect(42, 42, WIDTH - 84, HEIGHT - 84);

  let logo: HTMLImageElement | undefined;
  if (input.logo) {
    try {
      logo = await fileToImage(input.logo);
    } catch {
      logo = undefined;
    }
  }

  if (logo) drawContainedImage(context, logo, 105, 70, 120, 105);
  else drawOrbitMark(context, 162, 122, GOLD_LIGHT);

  context.fillStyle = GOLD_LIGHT;
  context.font = "600 33px Arial, sans-serif";
  context.fillText(input.values.collectionName.toUpperCase(), 255, 134);

  context.fillStyle = "#fbf8ef";
  context.font = "400 86px Georgia, serif";
  context.fillText("CERTIFICATE OF AUTHENTICITY", 105, 270);

  context.textAlign = "right";
  context.fillStyle = GOLD_LIGHT;
  context.font = "600 25px Arial, sans-serif";
  context.fillText("CERTIFICATE ID", 2080, 105);
  context.fillStyle = "#fbf8ef";
  context.font = "600 42px Georgia, serif";
  context.fillText(input.values.certificateId, 2080, 166);
  context.fillStyle = GOLD_LIGHT;
  context.font = "600 23px Arial, sans-serif";
  context.fillText(input.values.certificateStatus.toUpperCase(), 2080, 213);
  context.textAlign = "left";

  const titleSize = fitFontSize(context, input.values.meteoriteName, 1260, 118, "Georgia, serif");
  context.font = `400 ${titleSize}px Georgia, serif`;
  context.fillStyle = NAVY;
  context.fillText(input.values.meteoriteName, 112, 522);
  context.fillStyle = theme.accentText;
  context.font = "600 37px Arial, sans-serif";
  context.fillText(input.values.classification.toUpperCase(), 116, 590);
  context.strokeStyle = GOLD;
  context.lineWidth = 3;
  context.beginPath();
  context.moveTo(116, 625);
  context.lineTo(1420, 625);
  context.stroke();

  const photoFrame = { x: 1510, y: 426, width: 560, height: 455 };
  context.fillStyle = NAVY;
  roundedRect(context, photoFrame.x - 10, photoFrame.y - 10, photoFrame.width + 20, photoFrame.height + 20, 25);
  context.fill();
  context.save();
  roundedRect(context, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height, 17);
  context.clip();
  try {
    const photo = await fileToImage(input.mainPhoto);
    drawCoverImage(context, photo, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height);
  } catch {
    context.fillStyle = NAVY_SOFT;
    context.fillRect(photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height);
    context.fillStyle = "#ffffff";
    context.textAlign = "center";
    context.font = "600 25px Arial, sans-serif";
    context.fillText("ORIGINAL PHOTO PRESERVED IN PACKAGE", photoFrame.x + photoFrame.width / 2, photoFrame.y + 225);
    context.textAlign = "left";
  }
  context.restore();
  context.fillStyle = NAVY;
  context.font = "600 21px Arial, sans-serif";
  context.fillText("EXACT SPECIMEN PHOTOGRAPH", photoFrame.x, photoFrame.y + photoFrame.height + 42);

  const rows = [
    ["METEORITE", input.values.meteoriteName],
    ["FALL / FIND", input.values.fallStatus],
    ["DATE", displayDate(input.values.fallDate)],
    ["LOCALITY", input.values.locality],
    ["COORDINATES", `${input.values.latitude}, ${input.values.longitude}`],
    ["SPECIMEN FORM", input.values.specimenForm],
    ["RECORDED OWNER", input.values.recordedOwner],
  ];
  const tableX = 112;
  const tableY = 680;
  const tableWidth = 1308;
  const rowHeight = 82;
  context.strokeStyle = `${GOLD}8c`;
  context.lineWidth = 2;
  roundedRect(context, tableX, tableY, tableWidth, rows.length * rowHeight, 18);
  context.stroke();
  context.beginPath();
  context.moveTo(tableX + 395, tableY);
  context.lineTo(tableX + 395, tableY + rows.length * rowHeight);
  context.stroke();

  rows.forEach(([label, value], index) => {
    const y = tableY + index * rowHeight;
    if (index % 2 === 1) {
      context.fillStyle = `${GOLD}17`;
      context.fillRect(tableX + 2, y, tableWidth - 4, rowHeight);
    }
    context.fillStyle = NAVY;
    context.font = "700 25px Arial, sans-serif";
    context.fillText(label, tableX + 38, y + 52);
    context.fillStyle = INK;
    context.font = "400 31px Georgia, serif";
    const displayed = value.length > 55 ? `${value.slice(0, 52)}...` : value;
    context.fillText(displayed, tableX + 440, y + 53);
  });

  roundedRect(context, 1510, 960, 560, 212, 25);
  context.fillStyle = NAVY;
  context.fill();
  context.strokeStyle = GOLD_LIGHT;
  context.lineWidth = 4;
  roundedRect(context, 1526, 976, 528, 180, 18);
  context.stroke();
  context.fillStyle = GOLD_LIGHT;
  context.textAlign = "center";
  context.font = "600 23px Arial, sans-serif";
  context.fillText("RECORDED WEIGHT", 1790, 1018);
  context.fillStyle = "#ffffff";
  context.font = "400 72px Georgia, serif";
  context.fillText(`${input.values.weightGrams} g`, 1790, 1092);
  context.fillStyle = GOLD_LIGHT;
  context.font = "600 22px Arial, sans-serif";
  context.fillText(input.values.specimenForm.toUpperCase(), 1790, 1132);
  context.textAlign = "left";

  const qrDataUrl = await QRCode.toDataURL(input.qrPayload, {
    errorCorrectionLevel: "M",
    margin: 2,
    width: 310,
    color: { dark: NAVY, light: "#ffffff" },
  });
  const qr = await dataUrlToImage(qrDataUrl);
  roundedRect(context, 1734, 1226, 336, 336, 18);
  context.fillStyle = "#ffffff";
  context.fill();
  context.strokeStyle = GOLD;
  context.lineWidth = 3;
  context.stroke();
  context.drawImage(qr, 1747, 1239, 310, 310);

  context.fillStyle = NAVY;
  context.font = "600 22px Arial, sans-serif";
  context.fillText("DIGITALLY SIGNED BY", 112, 1335);
  context.font = "400 42px Georgia, serif";
  context.fillText(input.values.issuerName, 112, 1390);
  context.fillStyle = MUTED;
  context.font = "400 23px Arial, sans-serif";
  context.fillText(input.values.collectionName, 112, 1428);

  context.fillStyle = NAVY;
  context.font = "600 22px Arial, sans-serif";
  context.fillText("ISSUED", 730, 1335);
  context.font = "400 37px Georgia, serif";
  context.fillText(displayDate(input.values.issueDate), 730, 1390);
  context.fillStyle = MUTED;
  context.font = "400 22px Arial, sans-serif";
  context.fillText(`Version ${input.values.certificateVersion}`, 730, 1428);

  context.fillStyle = NAVY;
  context.font = "600 22px Arial, sans-serif";
  context.fillText("PROVENANCE", 112, 1495);
  context.fillStyle = INK;
  context.font = "400 22px Arial, sans-serif";
  wrapText(context, input.values.provenance, 112, 1534, 1490, 30, 2);

  context.fillStyle = MUTED;
  context.font = `400 ${certificateFooterLayout.fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillText(`Record SHA-256: ${input.recordHash}`, 112, certificateFooterLayout.recordHashBaseline);
  context.fillText(`Key FP: ${input.fingerprint}`, 112, certificateFooterLayout.keyFingerprintBaseline);

  if (input.values.certificateStatus !== "active") {
    context.save();
    context.translate(1250, 520);
    context.rotate(-0.11);
    context.strokeStyle = "rgba(139, 42, 42, 0.85)";
    context.fillStyle = "rgba(139, 42, 42, 0.08)";
    context.lineWidth = 8;
    roundedRect(context, -220, -58, 440, 116, 10);
    context.fill();
    context.stroke();
    context.fillStyle = "rgba(139, 42, 42, 0.9)";
    context.textAlign = "center";
    context.font = "700 48px Arial, sans-serif";
    context.fillText(input.values.certificateStatus.toUpperCase(), 0, 17);
    context.restore();
  }

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
