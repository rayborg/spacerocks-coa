import { jsPDF } from "jspdf";
import QRCode from "qrcode";
import type { FormValues } from "../types";
import { certificateFooterLayout, getCertificateTheme } from "../certificateThemes";
import type { CertificateStyleId } from "../certificateStyles";
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
    context.globalAlpha = 0.12;
    context.lineWidth = 2;
    for (let radius = 130; radius <= 360; radius += 38) {
      context.beginPath();
      context.ellipse(1600, 1320, radius, radius * 0.58, -0.22, 0, Math.PI * 2);
      context.stroke();
    }
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
  }
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
  const certificateStyle = input.values.certificateStyle;
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
    context.fillRect(1540, 48, 612, 302);
    context.fillStyle = GOLD;
    context.fillRect(72, 48, 10, 302);
    context.fillRect(1527, 48, 5, 302);
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
    const headerGradient = context.createLinearGradient(0, 0, 0, 372);
    headerGradient.addColorStop(0, NAVY_SOFT);
    headerGradient.addColorStop(1, NAVY);
    context.fillStyle = headerGradient;
    context.fillRect(0, 0, WIDTH, 372);
    context.strokeStyle = GOLD;
    context.lineWidth = 3;
    context.strokeRect(72, 38, WIDTH - 144, 296);
    context.beginPath();
    context.moveTo(WIDTH / 2 - 110, 350);
    context.lineTo(WIDTH / 2 + 110, 350);
    context.stroke();
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

  if (logo) {
    if (certificateStyle === "museum-ledger") {
      context.fillStyle = "#ffffff99";
      context.fillRect(100, 61, 132, 122);
      context.strokeStyle = GOLD;
      context.lineWidth = 3;
      context.strokeRect(100, 61, 132, 122);
    } else if (certificateStyle === "celestial-formal") {
      context.strokeStyle = `${GOLD_LIGHT}99`;
      context.lineWidth = 2;
      context.beginPath();
      context.ellipse(165, 122, 72, 61, -0.18, 0, Math.PI * 2);
      context.stroke();
    }
    drawContainedImage(context, logo, 105, 70, 120, 105);
  } else if (certificateStyle === "museum-ledger") drawLedgerMark(context, 162, 122, NAVY);
  else drawOrbitMark(context, 162, 122, GOLD_LIGHT);

  const headerAccent = certificateStyle === "museum-ledger" ? theme.accentText : GOLD_LIGHT;
  const headerInk = certificateStyle === "museum-ledger" ? NAVY : "#fbf8ef";
  context.fillStyle = headerAccent;
  context.font = "600 28px Arial, sans-serif";
  context.fillText(input.values.collectionName.toUpperCase(), 255, 134);

  context.fillStyle = headerAccent;
  context.font = "700 18px Arial, sans-serif";
  context.fillText("ARCHIVAL SPECIMEN RECORD", 105, 211);

  context.fillStyle = headerInk;
  const certificateTitle = "CERTIFICATE OF AUTHENTICITY";
  const certificateTitleSize = fitFontSize(
    context,
    certificateTitle,
    certificateStyle === "museum-ledger" ? 1370 : 1450,
    72,
    "Georgia, serif",
  );
  context.font = `400 ${certificateTitleSize}px Georgia, serif`;
  context.fillText(certificateTitle, 105, 286);

  context.textAlign = "right";
  context.fillStyle = certificateStyle === "museum-ledger" ? GOLD_LIGHT : headerAccent;
  context.font = "600 21px Arial, sans-serif";
  context.fillText("CERTIFICATE ID", 2080, 105);
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
  context.fillText(input.values.meteoriteName, 112, 522);
  context.fillStyle = theme.accentText;
  context.font = "600 31px Arial, sans-serif";
  context.fillText(input.values.classification.toUpperCase(), 116, 590);
  context.strokeStyle = certificateStyle === "museum-ledger" ? NAVY : GOLD;
  context.lineWidth = certificateStyle === "museum-ledger" ? 4 : certificateStyle === "regal-archive" ? 7 : 3;
  context.beginPath();
  context.moveTo(116, 625);
  context.lineTo(1420, 625);
  context.stroke();

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
  try {
    const photo = await fileToImage(input.mainPhoto);
    drawCoverImage(context, photo, photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height);
  } catch {
    context.fillStyle = NAVY_SOFT;
    context.fillRect(photoFrame.x, photoFrame.y, photoFrame.width, photoFrame.height);
    context.fillStyle = "#ffffff";
    context.textAlign = "center";
    context.font = "600 21px Arial, sans-serif";
    context.fillText("ORIGINAL PHOTO PRESERVED IN PACKAGE", photoFrame.x + photoFrame.width / 2, photoFrame.y + 225);
    context.textAlign = "left";
  }
  context.restore();
  context.fillStyle = NAVY;
  context.font = "600 18px Arial, sans-serif";
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
  const labelColumnWidth = certificateStyle === "museum-ledger" ? 345 : 370;
  if (certificateStyle === "museum-ledger") {
    context.fillStyle = "#ffffff99";
    context.fillRect(tableX, tableY, tableWidth, rows.length * rowHeight);
    context.fillStyle = NAVY;
    context.font = "700 17px Arial, sans-serif";
    context.fillText("OBJECT CATALOG / SIGNED SPECIMEN RECORD", tableX, tableY - 25);
  } else if (certificateStyle === "celestial-formal") {
    context.fillStyle = `${NAVY}0d`;
    roundedRect(context, tableX, tableY, tableWidth, rows.length * rowHeight, 22);
    context.fill();
  }
  context.strokeStyle = certificateStyle === "museum-ledger" ? NAVY : `${GOLD}a6`;
  context.lineWidth = certificateStyle === "regal-archive" ? 5 : certificateStyle === "museum-ledger" ? 2 : 3;
  roundedRect(context, tableX, tableY, tableWidth, rows.length * rowHeight, certificateStyle === "museum-ledger" ? 0 : 18);
  context.stroke();
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
    context.fillStyle = NAVY;
    context.fill();
    context.strokeStyle = GOLD_LIGHT;
    context.lineWidth = 4;
    roundedRect(context, 1526, 976, 528, 180, Math.max(0, detailRadius - 4));
    context.stroke();
  }
  context.fillStyle = GOLD_LIGHT;
  context.textAlign = "center";
  context.font = "600 19px Arial, sans-serif";
  context.fillText("SPECIMEN DETAILS", 1790, 1018);
  context.fillStyle = "#ffffff";
  context.font = "400 59px Georgia, serif";
  context.fillText(`${input.values.weightGrams} g`, 1790, 1092);
  context.fillStyle = GOLD_LIGHT;
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
  wrapText(context, input.values.provenance, 112, 1534, 1490, 27, 2);

  context.fillStyle = MUTED;
  context.font = `400 ${certificateFooterLayout.fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillText(`Record SHA-256: ${input.recordHash}`, 112, certificateFooterLayout.recordHashBaseline);
  context.fillText(`Key FP: ${input.fingerprint}`, 112, certificateFooterLayout.keyFingerprintBaseline);

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
