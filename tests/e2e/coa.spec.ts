import { expect, test, type Locator } from "@playwright/test";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import JSZip from "jszip";
import { CERTIFICATE_EXPORT_FONT_FLOOR } from "../../src/lib/certificate";
import type { FormValues } from "../../src/types";
import { solidPng } from "./image-fixtures";

const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const certificatePhotoPng = solidPng(560, 455);

const wideTransparentLogoSvg = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="36" viewBox="0 0 240 36"><path fill="#b87518" d="M0 16h240v4H0z"/><circle cx="120" cy="18" r="11" fill="#061a33"/></svg>',
);

const tallTransparentLogoSvg = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="180" viewBox="0 0 30 180"><path fill="#b87518" d="M13 0h4v180h-4z"/><circle cx="15" cy="90" r="13" fill="#061a33"/></svg>',
);

const themeCases = [
  ["Observatory Navy", "observatory-navy", ["#061a33", "#123a63", "#b87518", "#f2c66d", "#f7f0de"]],
  ["Museum Burgundy", "museum-burgundy", ["#400817", "#731c32", "#a8501c", "#f2b96b", "#faeee4"]],
  ["Field Archive", "field-archive", ["#123728", "#285d44", "#85751a", "#d9cf72", "#eef1df"]],
  ["Charcoal Gold", "charcoal-gold", ["#17191d", "#363b44", "#c39a32", "#efd177", "#f2efe7"]],
  ["Desert Copper", "desert-copper", ["#542011", "#82402a", "#bd5b23", "#f0aa69", "#f8e5cc"]],
  ["Celestial Teal", "celestial-teal", ["#00383f", "#00606a", "#bf7a18", "#f2bd62", "#e7f4f1"]],
  ["Royal Amethyst", "royal-amethyst", ["#2d0e52", "#57337b", "#a96919", "#e9bc67", "#f3eafa"]],
  ["Arctic Slate", "arctic-slate", ["#173549", "#315c70", "#89641d", "#dfc27c", "#eaf4f7"]],
  ["Monochrome Ink", "monochrome-ink", ["#101112", "#323538", "#5b6065", "#d6d8da", "#f6f6f3"]],
] as const;

const certificateStyleCases = [
  ["Celestial Formal", "celestial-formal"],
  ["Museum Type", "museum-type"],
] as const;

async function selectCertificateStyle(stylePicker: Locator, preview: Locator, name: string, id: string): Promise<void> {
  const radio = stylePicker.getByRole("radio", { name });
  // Rendering tests bypass pointer hit-testing on the transparent, animated style cards.
  await radio.evaluate((element: HTMLInputElement) => element.click());
  await expect(radio).toBeChecked();
  await expect(preview).toHaveAttribute("data-certificate-style", id);
}

interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

function boxesIntersect(left: BoundingBox, right: BoundingBox): boolean {
  return left.x < right.x + right.width
    && left.x + left.width > right.x
    && left.y < right.y + right.height
    && left.y + left.height > right.y;
}

const contentFieldNames = [
  "issuerName",
  "collectionName",
  "issuerEmail",
  "issuerPhone",
  "issuerAddress",
  "issuerWebsite",
  "certificateId",
  "issueDate",
  "certificateVersion",
  "supersededCertificateId",
  "certificateNotes",
  "meteoriteName",
  "suspectedType",
  "weightGrams",
  "weightPrecision",
  "dimensions",
  "numberOfPieces",
  "preparationState",
  "identifyingMarks",
  "fallStatus",
  "fallDate",
  "country",
  "region",
  "locality",
  "latitude",
  "longitude",
  "finderName",
  "recoveryInformation",
  "provenance",
  "previousOwner",
  "intermediaryPurchaserName",
  "buyer",
  "transferDate",
  "invoiceReference",
  "transferNotes",
] as const;

const optionalContentFieldNames = [
  "issuerEmail",
  "issuerPhone",
  "issuerAddress",
  "issuerWebsite",
  "certificateNotes",
  "dimensions",
  "preparationState",
  "identifyingMarks",
  "fallDate",
  "region",
  "locality",
  "latitude",
  "longitude",
  "suspectedType",
  "finderName",
  "recoveryInformation",
  "provenance",
  "previousOwner",
  "intermediaryPurchaserName",
  "buyer",
  "transferDate",
  "invoiceReference",
  "transferNotes",
] as const;

function duplicateCentralDirectoryEntry(zip: Buffer, targetSuffix: string): Buffer {
  let eocdOffset = -1;
  for (let offset = zip.length - 22; offset >= Math.max(0, zip.length - 65_557); offset -= 1) {
    if (zip.readUInt32LE(offset) === 0x06054b50) {
      eocdOffset = offset;
      break;
    }
  }
  if (eocdOffset < 0) throw new Error("EOCD not found in generated test ZIP");
  const centralOffset = zip.readUInt32LE(eocdOffset + 16);
  const centralSize = zip.readUInt32LE(eocdOffset + 12);
  const entryCount = zip.readUInt16LE(eocdOffset + 10);
  let cursor = centralOffset;
  let duplicate: Buffer | undefined;
  for (let index = 0; index < entryCount; index += 1) {
    if (zip.readUInt32LE(cursor) !== 0x02014b50) throw new Error("Invalid central directory in test ZIP");
    const nameLength = zip.readUInt16LE(cursor + 28);
    const extraLength = zip.readUInt16LE(cursor + 30);
    const commentLength = zip.readUInt16LE(cursor + 32);
    const length = 46 + nameLength + extraLength + commentLength;
    const name = zip.subarray(cursor + 46, cursor + 46 + nameLength).toString("utf8");
    if (name.endsWith(targetSuffix)) duplicate = Buffer.from(zip.subarray(cursor, cursor + length));
    cursor += length;
  }
  if (!duplicate) throw new Error(`Target entry ${targetSuffix} not found`);
  const result = Buffer.concat([zip.subarray(0, eocdOffset), duplicate, zip.subarray(eocdOffset)]);
  const nextEocdOffset = eocdOffset + duplicate.length;
  result.writeUInt16LE(entryCount + 1, nextEocdOffset + 8);
  result.writeUInt16LE(entryCount + 1, nextEocdOffset + 10);
  result.writeUInt32LE(centralSize + duplicate.length, nextEocdOffset + 12);
  return result;
}

test("starts with blank content, provisional preview labels, and readable responsive controls", async ({ page }) => {
  await page.goto("/#builder");

  for (const name of contentFieldNames) {
    const field = page.locator(`[name="${name}"]`);
    await expect(field, `${name} starts empty`).toHaveValue("");
    await expect(field, `${name} has a placeholder`).toHaveAttribute("placeholder", /\S/);
  }

  const specimenForm = page.getByLabel("Specimen form");
  await expect(specimenForm).toHaveValue("");
  await expect(specimenForm.locator('option[value=""]')).toHaveAttribute("disabled", "");
  await expect(specimenForm.locator('option[value=""]')).toHaveText("Select specimen form");
  await expect(page.locator('select[name="certificateStatus"]')).toHaveValue("active");
  await expect(page.getByRole("radio", { name: /Unclassified/ })).toBeChecked();
  await expect(page.getByRole("radio", { name: /Official/ })).not.toBeChecked();
  const templatePicker = page.getByRole("group", { name: "Certificate template" });
  await expect(templatePicker.getByRole("radio")).toHaveCount(2);
  await expect(templatePicker.getByRole("radio", { name: /Celestial Formal/ })).toBeChecked();
  await expect(templatePicker.getByRole("radio", { name: /Museum Type/ })).not.toBeChecked();
  await expect(templatePicker.getByRole("radio", { name: /Regal Archive|Museum Ledger/ })).toHaveCount(0);
  await expect(page.getByRole("radio", { name: /Observatory Navy/ })).toBeChecked();

  await expect(page.getByLabel("Issuer display or legal name")).toHaveAttribute("placeholder", "e.g., John Doe");
  await expect(page.getByLabel("Working specimen name")).toHaveAttribute("placeholder", "e.g., Unclassified specimen 001");
  await expect(page.getByLabel("Weight (grams)")).toHaveAttribute("placeholder", "e.g., 44.7");
  await expect(page.getByLabel("Issuer display or legal name")).toHaveValue("");
  await expect(page.getByLabel("Working specimen name")).toHaveValue("");
  await expect(page.getByLabel("Weight (grams)")).toHaveValue("");
  for (const name of optionalContentFieldNames) {
    const field = page.locator(`[name="${name}"]`);
    await expect(field, `${name} marks its placeholder optional`).toHaveAttribute("placeholder", /^Optional -/);
    await expect(field.locator("xpath=ancestor::label[1]").locator(".field__label"), `${name} marks its label optional`)
      .toContainText("(optional)");
  }
  await expect(page.getByLabel("Logo (optional)")).toHaveCount(1);
  await expect(page.getByText("Optional - included and hashed in the package.")).toHaveCount(1);
  await expect(page.locator('input[name="supersededCertificateId"]')).not.toHaveAttribute("placeholder", /^Optional/);
  await expect(page.locator('input[name="supersededCertificateId"]'))
    .toHaveAttribute("placeholder", "Required only for Superseded status");

  const preview = page.locator(".certificate-preview");
  await expect(preview.locator(".certificate-preview__collection")).toContainText("Collection name");
  await expect(preview.locator(".certificate-preview__title h3")).toHaveText("Meteorite name");
  await expect(preview.locator(".certificate-preview__title p")).toHaveText("Unclassified");
  await expect(preview.locator(".certificate-preview__id")).toContainText("Pending");
  await expect(preview.locator(".certificate-preview__facts")).toContainText("Not entered");
  await expect(preview.locator(".certificate-preview__facts")).toContainText("Specimen form");
  const specimenSummary = preview.locator(".certificate-preview__weight");
  await expect(specimenSummary).toHaveAttribute("data-specimen-state", "empty");
  await expect(specimenSummary.locator("span")).toHaveText("Recorded specimen");
  await expect(specimenSummary.locator("strong")).toHaveText("Awaiting details");
  await expect(specimenSummary.locator("em")).toHaveCount(0);
  await expect(specimenSummary).not.toContainText(/--|0 g|Specimen form/);
  await expect(preview.locator(".certificate-preview__signoff strong")).toHaveText("Issuer");

  await expect(page.getByRole("button", { name: "Issue cryptographically signed COA package" })).toBeDisabled();
  await expect(page.getByText("Complete these requirements before issuance:")).toBeVisible();
  const readiness = page.locator("#issuance-readiness");
  await expect(readiness).toContainText("Issuer name:");
  await expect(readiness).toContainText("Certificate ID:");
  await expect(readiness).toContainText("Meteorite name:");
  await expect(readiness).toContainText("Weight:");
  await expect(readiness).not.toContainText("Complete the required form fields.");
  await page.getByRole("button", { name: "Review missing form fields" }).click();
  await expect(page.locator(".generation-status")).toHaveText("Review the highlighted required fields.");
  await expect(page.getByLabel("Issuer display or legal name")).toHaveValue("");

  await page.locator(".photo-drop input[type=file]").setInputFiles({
    name: "filename-must-not-become-caption.png",
    mimeType: "image/png",
    buffer: certificatePhotoPng,
    lastModified: Date.UTC(2024, 0, 15),
  });
  const photoCaption = page.getByLabel("Caption (optional)", { exact: true });
  const photoCaptureDate = page.getByLabel("Capture date (optional)", { exact: true });
  await expect(page.locator(".photo-item__meta strong")).toHaveText("filename-must-not-become-caption.png");
  await expect(photoCaption).toHaveValue("");
  await expect(photoCaptureDate).toHaveValue("");
  await expect(photoCaption).toHaveAttribute("placeholder", "Optional - e.g., front face");
  await expect(photoCaptureDate).toHaveAttribute("placeholder", "Optional - e.g., 2026-07-29");

  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();

  for (const width of [2048, 1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    const styles = await page.evaluate(() => {
      const fontSize = (selector: string) => Number.parseFloat(getComputedStyle(document.querySelector(selector)!).fontSize);
      const visibleControls = Array.from(document.querySelectorAll<HTMLElement>(
        ".field input:not([type=file]):not([type=radio]), .field select, .field textarea, .photo-item input:not([type=checkbox])",
      )).filter((element) => element.getClientRects().length > 0);
      const photoCard = document.querySelector<HTMLElement>(".photo-item")!;
      const photoCardBox = photoCard.getBoundingClientRect();
      return {
        controlSizes: [
          fontSize('input[name="issuerName"]'),
          fontSize('select[name="specimenForm"]'),
          fontSize('textarea[name="provenance"]'),
        ],
        photoControlSizes: [fontSize(".photo-item input"), fontSize('.photo-item input[type="date"]')],
        labelSize: fontSize(".field__label"),
        hintSize: fontSize(".theme-field__hint"),
        styleDescriptionSize: fontSize(".style-option__body > small"),
        themeDescriptionSize: fontSize(".theme-option__body > small"),
        supportTextSizes: [
          fontSize(".workbench-section summary small"),
          fontSize(".key-state"),
          fontSize(".key-option .button"),
          fontSize(".generation-status"),
          fontSize(".issue-section > div:first-child > span"),
          fontSize(".preview-checks span"),
          fontSize(".preview-note p"),
        ],
        pageWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        photoCardFits: photoCardBox.left >= 0 && photoCardBox.right <= window.innerWidth
          && photoCard.scrollWidth <= photoCard.clientWidth,
        overflowingControls: visibleControls.filter((element) => {
          const box = element.getBoundingClientRect();
          return box.left < 0 || box.right > window.innerWidth || element.scrollWidth > element.clientWidth + 1;
        }).map((element) => ({
          name: element.getAttribute("name"),
          type: element.getAttribute("type"),
          className: element.className,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        })),
      };
    });
    const minimumControlSize = width <= 760 ? 16 : 14;
    for (const size of styles.controlSizes) {
      expect(size, `control font at ${width}px`).toBeGreaterThanOrEqual(minimumControlSize);
      expect(size, `control font at ${width}px`).toBeLessThanOrEqual(18);
    }
    for (const size of styles.photoControlSizes) {
      expect(size, `photo control font at ${width}px`).toBeGreaterThanOrEqual(minimumControlSize);
      expect(size, `photo control font at ${width}px`).toBeLessThanOrEqual(18);
    }
    expect(styles.labelSize, `label font at ${width}px`).toBeGreaterThanOrEqual(11);
    expect(styles.hintSize, `hint font at ${width}px`).toBeGreaterThanOrEqual(11);
    expect(styles.styleDescriptionSize, `style description font at ${width}px`).toBeGreaterThanOrEqual(11);
    expect(styles.themeDescriptionSize, `theme description font at ${width}px`).toBeGreaterThanOrEqual(11);
    for (const size of styles.supportTextSizes) {
      expect(size, `support text font at ${width}px`).toBeGreaterThanOrEqual(11);
    }
    expect(styles.pageWidth, `page overflow at ${width}px`).toBeLessThanOrEqual(styles.viewportWidth);
    expect(styles.photoCardFits, `photo card overflow at ${width}px`).toBe(true);
    expect(styles.overflowingControls, `control overflow at ${width}px`).toEqual([]);
    await expect(preview).toBeVisible();
    if (width === 2048 || width === 390) {
      const optionalPresentation = await page.evaluate((names) => names.map((name) => {
        const control = document.querySelector<HTMLElement>(`[name="${name}"]`)!;
        const wrapper = control.closest("label")!;
        return {
          name,
          visible: wrapper.getClientRects().length > 0,
          label: wrapper.querySelector(".field__label")?.textContent ?? "",
          placeholder: control.getAttribute("placeholder") ?? "",
        };
      }), optionalContentFieldNames);
      expect(optionalPresentation, `optional field presentation at ${width}px`).toEqual(
        optionalContentFieldNames.map((name) => expect.objectContaining({
          name,
          visible: true,
          label: expect.stringContaining("(optional)"),
          placeholder: expect.stringMatching(/^Optional -/),
        })),
      );
      await expect(photoCaption).toBeVisible();
      await expect(photoCaptureDate).toBeVisible();
    }
  }
});

test("accepts decoded photo shapes, previews a contain fit, and cleans object URLs", async ({ page }) => {
  await page.addInitScript(() => {
    const trackedWindow = window as typeof window & { __revokedPhotoUrls?: string[] };
    trackedWindow.__revokedPhotoUrls = [];
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url) => {
      trackedWindow.__revokedPhotoUrls!.push(url);
      revoke(url);
    };
  });
  await page.goto("/#builder");

  const requirements = page.locator(".photo-requirements");
  await expect(requirements).toContainText("112:91 landscape");
  await expect(requirements).toContainText("1120 x 910 px or larger");
  await expect(requirements).toContainText("Higher resolution improves print quality");
  await expect(requirements).toContainText("embedded DPI is not trusted");
  await expect(requirements).toContainText("without cropping, changing its width:height ratio, or distortion");
  await expect(requirements).toContainText("source file remains unchanged");

  const upload = page.locator(".photo-drop input[type=file]");
  await upload.setInputFiles({ name: "square-low-res.png", mimeType: "image/png", buffer: solidPng(8, 8) });
  const squareCard = page.locator(".photo-item").first();
  await expect(squareCard.locator(".photo-analysis")).toContainText("Accepted for contained display");
  await expect(squareCard.locator(".photo-analysis")).toContainText("8 x 8 px");
  await expect(squareCard.locator(".photo-analysis")).toContainText("complete image will be centered and contained");
  const previewUrl = await squareCard.locator("img").getAttribute("src");
  expect(previewUrl).toMatch(/^blob:/);

  await upload.setInputFiles([
    { name: "extreme-wide.png", mimeType: "image/png", buffer: solidPng(1200, 20) },
    { name: "extreme-tall.png", mimeType: "image/png", buffer: solidPng(20, 1200) },
    { name: "portrait.png", mimeType: "image/png", buffer: solidPng(400, 800) },
  ]);
  await expect(page.locator(".photo-item")).toHaveCount(4);
  for (const card of await page.locator(".photo-item").all()) {
    await expect(card.locator(".photo-analysis")).toContainText("Accepted for contained display");
  }

  const containViewport = page.locator(".certificate-preview__photo-viewport");
  await expect(containViewport).toHaveAttribute("data-photo-fit", "contain");
  const containGeometry = await containViewport.evaluate((element) => {
    const viewport = element.getBoundingClientRect();
    const image = element.querySelector("img") as HTMLImageElement;
    const style = getComputedStyle(image);
    return {
      viewportRatio: viewport.width / viewport.height,
      naturalRatio: image.naturalWidth / image.naturalHeight,
      objectFit: style.objectFit,
      objectPosition: style.objectPosition,
    };
  });
  expect(containGeometry.viewportRatio).toBeCloseTo(112 / 91, 2);
  expect(containGeometry.naturalRatio).toBe(1);
  expect(containGeometry.objectFit).toBe("contain");
  expect(containGeometry.objectPosition).toBe("50% 50%");

  await upload.setInputFiles({
    name: "spoofed.png",
    mimeType: "image/png",
    buffer: Buffer.from("not an image"),
  });
  await expect(page.locator(".inline-status")).toContainText("encoded file signature does not match");
  await upload.setInputFiles({
    name: "unsupported.gif",
    mimeType: "image/gif",
    buffer: Buffer.from("GIF89a"),
  });
  await expect(page.locator(".inline-status")).toContainText("unsupported MIME type");
  await expect(page.locator(".photo-item")).toHaveCount(4);

  const currentPreviewUrl = await page.locator(".photo-item").first().locator("img").getAttribute("src");
  await page.locator(".photo-item").first().getByRole("button", { name: "Remove square-low-res.png" }).click();
  await expect(page.getByText("Primary photo blocks issuance")).toHaveCount(0);
  await expect(page.locator(".certificate-preview__photo-viewport img")).toHaveAttribute("src", /blob:/);
  await expect.poll(() => page.evaluate((url) => {
    const trackedWindow = window as typeof window & { __revokedPhotoUrls?: string[] };
    return trackedWindow.__revokedPhotoUrls?.includes(url!) ?? false;
  }, currentPreviewUrl)).toBe(true);
});

test("requires a superseded certificate ID only for superseded status", async ({ page }) => {
  await page.goto("/#builder");
  const status = page.locator('select[name="certificateStatus"]');
  const supersededId = page.locator('input[name="supersededCertificateId"]');
  const review = page.getByRole("button", { name: "Review missing form fields" });

  await status.selectOption("superseded");
  await review.click();
  await expect(supersededId.locator("xpath=ancestor::label[1]").locator(".field__error"))
    .toHaveText("Record the certificate ID this version supersedes.");

  await supersededId.fill("COA-2025-0001");
  await review.click();
  await expect(supersededId.locator("xpath=ancestor::label[1]").locator(".field__error")).toHaveCount(0);

  await supersededId.fill("");
  await status.selectOption("active");
  await review.click();
  await expect(supersededId.locator("xpath=ancestor::label[1]").locator(".field__error")).toHaveCount(0);
});

test("keeps the form before the live preview when the builder stacks", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/#builder");

  const workbench = await page.locator(".workbench").boundingBox();
  const preview = await page.locator(".preview-column").boundingBox();
  expect(workbench && preview).toBeTruthy();
  expect(preview!.y).toBeGreaterThanOrEqual(workbench!.y + workbench!.height);
});

test("enforces fresh official evidence across value and mode changes and validates location methods", async ({ page }) => {
  const lpiRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://www.lpi.usra.edu/")) lpiRequests.push(request.url());
  });
  await page.goto("/#builder");
  const provenance = page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" });
  await provenance.locator("summary").click();

  await page.getByRole("radio", { name: /Official/ }).check();
  await expect(page.locator('input[name="meteoriteType"]')).toHaveValue("Unclassified");
  await expect(page.locator('input[name="classification"]')).toHaveValue("Unclassified");
  await expect(page.locator('input[name="meteoriteSubclass"]')).toHaveValue("Unclassified");
  await expect(page.getByText("Enter the official value or attest below that the linked MetBull entry does not provide it.").first()).toBeVisible();

  await page.locator('input[name="meteoriteType"]').fill("Chondrite");
  await page.locator('input[name="classification"]').fill("Carbonaceous chondrite");
  await page.locator('input[name="meteoriteSubclass"]').fill("CM2");
  const officialUrl = page.locator('input[name="officialReferenceUrl"]');
  await officialUrl.fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063");
  await expect(page.locator('input[name="metbullCode"]')).toHaveValue("68063");
  const attestation = page.locator('input[name="officialNameVerified"]');
  await attestation.check();
  await expect(attestation).toBeChecked();
  await expect(page.getByRole("link", { name: "Open official Meteoritical Bulletin entry" }))
    .toHaveAttribute("href", "https://www.lpi.usra.edu/meteor/metbull.cfm?code=68063");
  expect(lpiRequests).toEqual([]);

  for (const [name, value] of [
    ["meteoriteName", "Aguas Zarcas revised"],
    ["meteoriteType", "Carbonaceous meteorite"],
    ["classification", "Carbonaceous chondrite revised"],
    ["meteoriteSubclass", "CM2-an"],
  ] as const) {
    await page.locator(`input[name="${name}"]`).fill(value);
    await expect(attestation, `${name} resets attestation`).not.toBeChecked();
    await attestation.check();
    await expect(attestation).toBeChecked();
  }

  await officialUrl.fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=68064");
  await expect(page.locator('input[name="metbullCode"]')).toHaveValue("68064");
  await expect(attestation).not.toBeChecked();
  await attestation.check();
  await officialUrl.fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=68065");
  await expect(attestation).not.toBeChecked();
  await officialUrl.fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=68064");
  await attestation.check();

  await page.getByRole("radio", { name: /Unclassified/ }).check();
  await expect(page.getByLabel("Suspected type (optional)")).toBeVisible();
  await page.getByRole("radio", { name: /Official/ }).check();
  await expect(page.locator('input[name="meteoriteType"]')).toHaveValue("Carbonaceous meteorite");
  await expect(page.locator('input[name="officialReferenceUrl"]'))
    .toHaveValue("https://www.lpi.usra.edu/meteor/metbull.cfm?code=68064");
  await expect(attestation).not.toBeChecked();
  await expect(page.getByText(/Attest that the official name and classification/)).toBeVisible();

  await page.locator('input[name="country"]').fill("Canada");
  await page.locator('input[name="locality"]').fill("Ottawa");
  await page.locator('input[name="region"]').fill("");
  await expect(page.locator(".certificate-preview__facts dd").nth(1)).toHaveText("Ottawa, Canada");
  await page.locator('input[name="locality"]').fill("");
  await page.locator('input[name="region"]').fill("Ontario");
  await expect(page.locator(".certificate-preview__facts dd").nth(1)).toHaveText("Ontario, Canada");
  await page.locator('input[name="region"]').fill("");
  await page.locator('input[name="latitude"]').fill("45.4215 N");
  await expect(page.getByText("Enter both latitude and longitude, or leave both blank.")).toBeVisible();
  await page.locator('input[name="longitude"]').fill("75.6972 W");
  await expect(page.getByText("Enter both latitude and longitude, or leave both blank.")).toHaveCount(0);
  await expect(page.locator(".certificate-preview__facts dd").nth(1)).toHaveText("Canada");
});

test("makes vertical continuation obvious and accessible from the hero", async ({ page }) => {
  const artifactDirectory = process.env.COA_ARTIFACT_DIR;
  if (artifactDirectory) await mkdir(artifactDirectory, { recursive: true });

  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1280 ? 900 : 844 });
    await page.goto("/");
    const hero = page.locator(".hero");
    const cue = page.getByRole("link", { name: "Explore below" });
    await expect(cue).toBeVisible();
    await expect(cue).toHaveAttribute("href", "#principles");
    await expect(page.locator("#principles")).toHaveCount(1);
    expect(await page.evaluate(() => window.scrollY), `initial scroll at ${width}px`).toBe(0);
    await hero.evaluate((element) => {
      document.documentElement.style.scrollBehavior = "auto";
      window.scrollTo(0, (element as HTMLElement).offsetTop);
    });
    await expect.poll(() => hero.evaluate((element) => Math.round(element.getBoundingClientRect().top))).toBeLessThanOrEqual(1);
    if (artifactDirectory) {
      await page.screenshot({ path: join(artifactDirectory, `hero-viewport-${width}.png`) });
    }

    const [heroBox, cueBox, contentBox, actionsBox, ledgerBox] = await Promise.all([
      hero.boundingBox(),
      cue.boundingBox(),
      page.locator(".hero__content").boundingBox(),
      page.locator(".hero__actions").boundingBox(),
      page.locator(".hero__ledger").boundingBox(),
    ]);
    expect(heroBox && cueBox && contentBox && actionsBox && ledgerBox, `hero geometry at ${width}px`).toBeTruthy();
    expect(cueBox!.y, `cue initial viewport top at ${width}px`).toBeGreaterThanOrEqual(0);
    expect(cueBox!.y + cueBox!.height, `cue initial viewport bottom at ${width}px`).toBeLessThanOrEqual(width === 1280 ? 900 : 844);
    expect(cueBox!.x, `cue left containment at ${width}px`).toBeGreaterThanOrEqual(heroBox!.x);
    expect(cueBox!.x + cueBox!.width, `cue right containment at ${width}px`).toBeLessThanOrEqual(heroBox!.x + heroBox!.width);
    expect(cueBox!.y, `cue top containment at ${width}px`).toBeGreaterThanOrEqual(heroBox!.y);
    expect(cueBox!.y + cueBox!.height, `cue bottom containment at ${width}px`).toBeLessThanOrEqual(heroBox!.y + heroBox!.height);
    expect(boxesIntersect(cueBox!, contentBox!), `cue/content overlap at ${width}px`).toBe(false);
    expect(boxesIntersect(cueBox!, actionsBox!), `cue/actions overlap at ${width}px`).toBe(false);
    expect(boxesIntersect(cueBox!, ledgerBox!), `cue/ledger overlap at ${width}px`).toBe(false);
    if (width <= 900) {
      expect(cueBox!.y, `mobile cue follows content at ${width}px`).toBeGreaterThanOrEqual(contentBox!.y + contentBox!.height);
      expect(ledgerBox!.y, `mobile ledger follows cue at ${width}px`).toBeGreaterThanOrEqual(cueBox!.y + cueBox!.height);
    }

    await cue.focus();
    await expect(cue).toBeFocused();

  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".hero__scroll-cue i")).toHaveCSS("animation-name", "none");
});

test("renders coherent specimen states and complete responsive certificate headings", async ({ page }) => {
  await page.goto("/#builder");
  const preview = page.locator(".certificate-preview");
  const summary = preview.locator(".certificate-preview__weight");
  const stylePicker = page.getByRole("group", { name: "Certificate template" });

  await expect(summary).toHaveClass(/certificate-preview__weight--empty/);
  for (const width of [2048, 1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    for (const [name, id] of certificateStyleCases) {
      await selectCertificateStyle(stylePicker, preview, name, id);
      const blankSummary = await summary.evaluate((element) => {
        const label = element.querySelector<HTMLElement>("span")!;
        const detail = element.querySelector<HTMLElement>("strong")!;
        const cardBox = element.getBoundingClientRect();
        const labelBox = label.getBoundingClientRect();
        const detailBox = detail.getBoundingClientRect();
        return {
          labelSize: Number.parseFloat(getComputedStyle(label).fontSize),
          detailSize: Number.parseFloat(getComputedStyle(detail).fontSize),
          labelFits: label.scrollWidth <= label.clientWidth,
          detailFits: detail.scrollWidth <= detail.clientWidth,
          labelContained: labelBox.left >= cardBox.left && labelBox.right <= cardBox.right
            && labelBox.top >= cardBox.top && labelBox.bottom <= cardBox.bottom,
          detailContained: detailBox.left >= cardBox.left && detailBox.right <= cardBox.right
            && detailBox.top >= cardBox.top && detailBox.bottom <= cardBox.bottom,
          cardOverflow: getComputedStyle(element).overflow,
          labelWhiteSpace: getComputedStyle(label).whiteSpace,
          detailWhiteSpace: getComputedStyle(detail).whiteSpace,
        };
      });
      expect(blankSummary.labelSize, `${name} blank label at ${width}px`).toBe(16);
      expect(blankSummary.detailSize, `${name} blank detail at ${width}px`).toBe(20);
      expect(blankSummary.labelFits, `${name} blank label clipping at ${width}px`).toBe(true);
      expect(blankSummary.detailFits, `${name} blank detail clipping at ${width}px`).toBe(true);
      expect(blankSummary.labelContained, `${name} blank label containment at ${width}px`).toBe(true);
      expect(blankSummary.detailContained, `${name} blank detail containment at ${width}px`).toBe(true);
      expect(blankSummary.cardOverflow).toBe("hidden");
      expect(blankSummary.labelWhiteSpace).toBe("nowrap");
      expect(blankSummary.detailWhiteSpace).toBe("nowrap");
    }
  }

  await page.getByLabel("Weight (grams)").fill("18.25");
  await expect(summary).toHaveAttribute("data-specimen-state", "partial");
  await expect(summary.locator("span")).toHaveText("Recorded weight");
  await expect(summary.locator("strong")).toHaveText("18.25 g");
  await expect(summary.locator("em")).toHaveCount(0);

  await page.getByLabel("Specimen form").selectOption({ label: "Half stone / end cut" });
  await expect(summary).toHaveAttribute("data-specimen-state", "complete");
  await expect(summary.locator("span")).toHaveText("Specimen details");
  await expect(summary.locator("strong")).toHaveText("18.25 g");
  await expect(summary.locator("em")).toHaveText("Half stone / end cut");

  await page.getByLabel("Weight (grams)").fill("");
  await expect(summary).toHaveAttribute("data-specimen-state", "partial");
  await expect(summary.locator("span")).toHaveText("Specimen form");
  await expect(summary.locator("strong")).toHaveText("Half stone / end cut");
  await expect(summary).not.toContainText(/--|0 g|Awaiting/);

  await page.getByLabel("Working specimen name").fill("Northwest Africa 15000");
  await page.getByLabel("Suspected type (optional)").fill("Lunar feldspathic breccia");
  await page.locator('input[name="certificateId"]').fill("MUS-2026-0042");
  await page.getByLabel("Collection or business").fill("Natural History Research Collection");
  await page.getByLabel("Weight (grams)").fill("18.25");

  const artifactDirectory = process.env.COA_ARTIFACT_DIR;
  if (artifactDirectory) await mkdir(artifactDirectory, { recursive: true });

  for (const width of [2048, 1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    let celestialPhotoAspectRatio: number | undefined;
    for (const [name, id] of certificateStyleCases) {
      await selectCertificateStyle(stylePicker, preview, name, id);
      const geometry = await preview.evaluate((element) => {
        const box = (selector: string) => element.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON();
        const fontSize = (selector: string) => Number.parseFloat(getComputedStyle(element.querySelector(selector)!).fontSize);
        const heading = element.querySelector<HTMLElement>(".certificate-preview__header > strong")!;
        const meteoriteName = element.querySelector<HTMLElement>(".certificate-preview__title h3")!;
        const recordType = element.querySelector<HTMLElement>(".certificate-preview__record-type")!;
        const collection = element.querySelector<HTMLElement>(".certificate-preview__collection")!;
        const collectionText = element.querySelector<HTMLElement>(".certificate-preview__collection > span:last-child")!;
        const idLabel = element.querySelector<HTMLElement>(".certificate-preview__id span")!;
        const idValue = element.querySelector<HTMLElement>(".certificate-preview__id > strong")!;
        const factLabels = Array.from(element.querySelectorAll<HTMLElement>(".certificate-preview__facts dt"));
        const factValues = Array.from(element.querySelectorAll<HTMLElement>(".certificate-preview__facts dd"));
        const frame = element.querySelector<HTMLElement>(".certificate-preview__frame")!;
        const canvas = element.querySelector<HTMLElement>(".certificate-preview__canvas")!;
        const certificateText = Array.from(frame.querySelectorAll<HTMLElement>(
          ".certificate-preview__collection, .certificate-preview__record-type, .certificate-preview__header > strong, .certificate-preview__id, .certificate-preview__id span, .certificate-preview__id > strong, .certificate-preview__title h3, .certificate-preview__title p, .certificate-preview__photo, .certificate-preview__photo-caption, .certificate-preview__facts dt, .certificate-preview__facts dd, .certificate-preview__weight span, .certificate-preview__weight strong, .certificate-preview__weight em, .certificate-preview__weight small, .certificate-preview__signoff span, .certificate-preview__signoff strong, .certificate-preview__signoff small, .certificate-preview__seal, .certificate-preview__seal small, .certificate-preview__status",
        ));
        const scale = Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--certificate-preview-scale"));
        const generatedFactsStyle = getComputedStyle(element.querySelector(".certificate-preview__facts")!, "::before");
        const fontSizes = certificateText.map((node) => Number.parseFloat(getComputedStyle(node).fontSize));
        if (generatedFactsStyle.content !== "none" && generatedFactsStyle.content !== "normal") {
          fontSizes.push(Number.parseFloat(generatedFactsStyle.fontSize));
        }
        const minimumFontSize = Math.min(...fontSizes);
        const photo = box(".certificate-preview__photo");
        return {
          headingText: heading.textContent,
          recordTypeText: recordType.textContent,
          idLabelText: idLabel.textContent,
          heading: heading.getBoundingClientRect().toJSON(),
          headingFits: heading.scrollWidth <= heading.clientWidth,
          meteoriteNameFits: meteoriteName.scrollWidth <= meteoriteName.clientWidth,
          collectionFits: collection.scrollWidth <= collection.clientWidth,
          collectionTextHandled: collectionText.scrollWidth <= collectionText.clientWidth
            || getComputedStyle(collectionText).textOverflow === "ellipsis",
          idLabelFits: idLabel.scrollWidth <= idLabel.clientWidth,
          idValueFits: idValue.scrollWidth <= idValue.clientWidth,
          factLabelsFit: factLabels.every((node) => node.scrollWidth <= node.clientWidth),
          factLabelMetrics: factLabels.map((node) => ({
            text: node.textContent,
            clientWidth: node.clientWidth,
            scrollWidth: node.scrollWidth,
          })),
          factValuesFit: factValues.every((node) => node.scrollWidth <= node.clientWidth),
          canonicalFrame: {
            width: Number.parseFloat(getComputedStyle(frame).width),
            height: Number.parseFloat(getComputedStyle(frame).height),
            scale,
            minimumFontSize,
            effectiveMinimumFontSize: scale * minimumFontSize,
          },
          fontSizes: {
            recordType: fontSize(".certificate-preview__record-type"),
            heading: fontSize(".certificate-preview__header > strong"),
            certificateId: fontSize(".certificate-preview__id"),
            meteoriteName: fontSize(".certificate-preview__title h3"),
            classification: fontSize(".certificate-preview__title p"),
            factLabel: fontSize(".certificate-preview__facts dt"),
            factValue: fontSize(".certificate-preview__facts dd"),
            specimenWeight: fontSize(".certificate-preview__weight strong"),
            signoff: fontSize(".certificate-preview__signoff strong"),
          },
          id: box(".certificate-preview__id"),
          photo,
          photoAspectRatio: photo.width / photo.height,
          facts: box(".certificate-preview__facts"),
          summary: box(".certificate-preview__weight"),
          signoff: box(".certificate-preview__signoff"),
          seal: box(".certificate-preview__seal"),
          frame: box(".certificate-preview__frame"),
        };
      });
      expect(geometry.headingText).toBe("Certificate of Authenticity");
      expect(geometry.recordTypeText).toBe(id === "museum-type" ? "Scientific specimen identification" : "Archival specimen record");
      expect(geometry.idLabelText).toBe(id === "museum-type" ? "Specimen record" : "Certificate ID");
      expect(geometry.headingFits, `${id} title clipping at ${width}px`).toBe(true);
      expect(geometry.meteoriteNameFits, `${id} representative meteorite name clipping at ${width}px`).toBe(true);
      expect(geometry.collectionFits, `${id} representative collection clipping at ${width}px`).toBe(true);
      expect(geometry.collectionTextHandled, `${id} representative collection text handling at ${width}px`).toBe(true);
      expect(geometry.idLabelFits, `${id} representative ID label clipping at ${width}px`).toBe(true);
      expect(geometry.idValueFits, `${id} representative ID clipping at ${width}px`).toBe(true);
      expect(
        geometry.factLabelsFit,
        `${id} representative fact label clipping at ${width}px: ${JSON.stringify(geometry.factLabelMetrics)}`,
      ).toBe(true);
      expect(geometry.factValuesFit, `${id} representative fact value clipping at ${width}px`).toBe(true);
      expect(geometry.canonicalFrame.width).toBe(1100);
      expect(geometry.canonicalFrame.height).toBe(850);
      expect(geometry.canonicalFrame.scale, `${id} scale at ${width}px`).toBeGreaterThan(0);
      expect(geometry.canonicalFrame.scale, `${id} scale at ${width}px`).toBeLessThanOrEqual(1);
      expect(geometry.canonicalFrame.minimumFontSize, `${id} canonical font floor at ${width}px`).toBeGreaterThanOrEqual(16);
      expect(geometry.canonicalFrame.effectiveMinimumFontSize, `${id} rendered font floor at ${width}px`).toBeGreaterThanOrEqual(12);
      if (id === "celestial-formal") celestialPhotoAspectRatio = geometry.photoAspectRatio;
      else expect(geometry.photoAspectRatio, `${id} photo ratio at ${width}px`).toBeCloseTo(celestialPhotoAspectRatio!, 2);
      for (const [level, size, minimum, maximum] of [
        ["record type", geometry.fontSizes.recordType, 16, 16],
        ["certificate heading", geometry.fontSizes.heading, 30, 30],
        ["certificate ID", geometry.fontSizes.certificateId, 24, 24],
        ["meteorite name", geometry.fontSizes.meteoriteName, 40, 40],
        ["classification", geometry.fontSizes.classification, 16, 16],
        ["fact label", geometry.fontSizes.factLabel, 16, 16],
        ["fact value", geometry.fontSizes.factValue, 18, 18],
        ["specimen weight", geometry.fontSizes.specimenWeight, 48, 48],
        ["signoff", geometry.fontSizes.signoff, 24, 24],
      ] as const) {
        expect(size, `${id} ${level} too small at ${width}px`).toBeGreaterThanOrEqual(minimum);
        expect(size, `${id} ${level} too large at ${width}px`).toBeLessThanOrEqual(maximum);
      }
      expect(boxesIntersect(geometry.heading, geometry.id), `${id} title/id at ${width}px`).toBe(false);
      expect(boxesIntersect(geometry.photo, geometry.summary), `${id} photo/summary at ${width}px`).toBe(false);
      expect(boxesIntersect(geometry.facts, geometry.signoff), `${id} facts/signoff at ${width}px`).toBe(false);
      expect(boxesIntersect(geometry.summary, geometry.seal), `${id} summary/seal at ${width}px`).toBe(false);
      for (const content of [geometry.heading, geometry.id, geometry.photo, geometry.facts, geometry.summary, geometry.signoff, geometry.seal]) {
        expect(content.x, `${id} content left at ${width}px`).toBeGreaterThanOrEqual(geometry.frame.x);
        expect(content.x + content.width, `${id} content right at ${width}px`).toBeLessThanOrEqual(geometry.frame.x + geometry.frame.width);
        expect(content.y, `${id} content top at ${width}px`).toBeGreaterThanOrEqual(geometry.frame.y);
        expect(content.y + content.height, `${id} content bottom at ${width}px`).toBeLessThanOrEqual(geometry.frame.y + geometry.frame.height);
      }
    }
  }

  await page.setViewportSize({ width: 2048, height: 1150 });
  await selectCertificateStyle(stylePicker, preview, "Museum Type", "museum-type");
  const fontInflationStyle = await page.addStyleTag({
    content: `
      .certificate-preview--museum-type .certificate-preview__collection,
      .certificate-preview--museum-type .certificate-preview__record-type,
      .certificate-preview--museum-type .certificate-preview__id span,
      .certificate-preview--museum-type .certificate-preview__id > strong,
      .certificate-preview--museum-type .certificate-preview__photo-caption {
        font-size: 24px !important;
      }
    `,
  });
  const inflatedMuseumHeader = await preview.evaluate((element) => {
    const header = element.querySelector<HTMLElement>(".certificate-preview__header")!.getBoundingClientRect();
    const title = element.querySelector<HTMLElement>(".certificate-preview__header > strong")!.getBoundingClientRect();
    const id = element.querySelector<HTMLElement>(".certificate-preview__id")!.getBoundingClientRect();
    const collection = element.querySelector<HTMLElement>(".certificate-preview__collection > span:last-child")!;
    const mark = element.querySelector<HTMLElement>(".certificate-preview__collection .orbit-mark")!;
    const markStyle = getComputedStyle(mark);
    const caption = element.querySelector<HTMLElement>(".certificate-preview__photo-caption")!;
    const textNodes = [
      element.querySelector<HTMLElement>(".certificate-preview__record-type")!,
      element.querySelector<HTMLElement>(".certificate-preview__id span")!,
      element.querySelector<HTMLElement>(".certificate-preview__id > strong")!,
      caption,
    ];
    return {
      overflowingText: textNodes.filter((node) => node.scrollWidth > node.clientWidth).map((node) => ({
        className: node.className,
        text: node.textContent,
        clientWidth: node.clientWidth,
        scrollWidth: node.scrollWidth,
      })),
      textStaysSingleLine: textNodes.every((node) => getComputedStyle(node).whiteSpace === "nowrap"),
      collectionOverflows: collection.scrollWidth > collection.clientWidth,
      collectionEllipsis: getComputedStyle(collection).textOverflow,
      markWidth: Number.parseFloat(markStyle.width),
      markHeight: Number.parseFloat(markStyle.height),
      markFlexShrink: markStyle.flexShrink,
      captionFits: caption.scrollWidth <= caption.clientWidth && caption.scrollHeight <= caption.clientHeight,
      captionWhiteSpace: getComputedStyle(caption).whiteSpace,
      idContained: id.left >= header.left - 1 && id.right <= header.right + 1
        && id.top >= header.top - 1 && id.bottom <= header.bottom + 1,
      titleAndIdDisjoint: !(title.left < id.right && title.right > id.left && title.top < id.bottom && title.bottom > id.top),
    };
  });
  expect(inflatedMuseumHeader.overflowingText).toEqual([]);
  expect(inflatedMuseumHeader.textStaysSingleLine).toBe(true);
  expect(inflatedMuseumHeader.collectionOverflows).toBe(true);
  expect(inflatedMuseumHeader.collectionEllipsis).toBe("ellipsis");
  expect(inflatedMuseumHeader.markWidth).toBe(20);
  expect(inflatedMuseumHeader.markHeight).toBe(20);
  expect(inflatedMuseumHeader.markFlexShrink).toBe("0");
  expect(inflatedMuseumHeader.captionFits).toBe(true);
  expect(inflatedMuseumHeader.captionWhiteSpace).toBe("nowrap");
  expect(inflatedMuseumHeader.idContained).toBe(true);
  expect(inflatedMuseumHeader.titleAndIdDisjoint).toBe(true);
  if (artifactDirectory) {
    await preview.screenshot({ path: join(artifactDirectory, "inflated-museum-header-2048.png") });
  }
  await fontInflationStyle.evaluate((element) => element.remove());

  const longCertificateId = `COA-${"X".repeat(116)}`;
  const longOwner = "Long-form collection owner name ".repeat(8).trim();
  await page.locator('input[name="certificateId"]').fill(longCertificateId);
  await page.getByLabel("Issuer display or legal name").fill(longOwner);
  await expect(preview.locator(".certificate-preview__id > strong")).toHaveText(longCertificateId);
  for (const [name, id] of certificateStyleCases) {
    await selectCertificateStyle(stylePicker, preview, name, id);
    const boundaryOverflow = await preview.evaluate((element) => {
      const frame = element.querySelector<HTMLElement>(".certificate-preview__frame")!.getBoundingClientRect();
      const idValue = element.querySelector<HTMLElement>(".certificate-preview__id > strong")!;
      const owner = element.querySelectorAll<HTMLElement>(".certificate-preview__facts dd")[3];
      const idBox = idValue.getBoundingClientRect();
      const ownerBox = owner.getBoundingClientRect();
      return {
        idOverflows: idValue.scrollWidth > idValue.clientWidth,
        idEllipsis: getComputedStyle(idValue).textOverflow,
        idContained: idBox.left >= frame.left && idBox.right <= frame.right,
        ownerOverflows: owner.scrollWidth > owner.clientWidth,
        ownerEllipsis: getComputedStyle(owner).textOverflow,
        ownerContained: ownerBox.left >= frame.left && ownerBox.right <= frame.right,
      };
    });
    expect(boundaryOverflow.idOverflows, `${id} long ID exercises ellipsis`).toBe(true);
    expect(boundaryOverflow.idEllipsis, `${id} long ID ellipsis`).toBe("ellipsis");
    expect(boundaryOverflow.idContained, `${id} long ID containment`).toBe(true);
    expect(boundaryOverflow.ownerOverflows, `${id} long owner exercises ellipsis`).toBe(true);
    expect(boundaryOverflow.ownerEllipsis, `${id} long owner ellipsis`).toBe("ellipsis");
    expect(boundaryOverflow.ownerContained, `${id} long owner containment`).toBe(true);
  }
  await page.locator('input[name="certificateId"]').fill("MUS-2026-0042");
  await page.getByLabel("Issuer display or legal name").fill("");

  await selectCertificateStyle(stylePicker, preview, "Museum Type", "museum-type");
  const museumSignature = await preview.evaluate((element) => {
    const frame = getComputedStyle(element.querySelector(".certificate-preview__frame")!);
    const id = getComputedStyle(element.querySelector(".certificate-preview__id")!);
    const facts = getComputedStyle(element.querySelector(".certificate-preview__facts")!);
    const factLabel = getComputedStyle(element.querySelector(".certificate-preview__facts dt")!);
    const photoCaption = element.querySelector<HTMLElement>(".certificate-preview__photo-caption")!;
    const weight = getComputedStyle(element.querySelector(".certificate-preview__weight")!);
    const seal = getComputedStyle(element.querySelector(".certificate-preview__seal")!);
    const catalogNote = element.querySelector<HTMLElement>(".certificate-preview__catalog-note")!;
    return {
      frameRadius: frame.borderRadius,
      frameBorder: frame.borderTopWidth,
      accessionBackground: id.backgroundColor,
      factsBorder: facts.borderTopWidth,
      factLabelColor: factLabel.color,
      photoCaption: photoCaption.textContent,
      photoCaptionFits: photoCaption.scrollWidth <= photoCaption.clientWidth && photoCaption.scrollHeight <= photoCaption.clientHeight,
      photoCaptionWhiteSpace: getComputedStyle(photoCaption).whiteSpace,
      measurementRail: weight.borderLeftWidth,
      sealRadius: seal.borderRadius,
      sealBorder: seal.borderTopWidth,
      catalogNote: catalogNote.textContent,
    };
  });
  expect(Number.parseFloat(museumSignature.frameRadius)).toBeGreaterThanOrEqual(20);
  expect(Number.parseFloat(museumSignature.frameBorder)).toBeGreaterThanOrEqual(7);
  expect(museumSignature.accessionBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(Number.parseFloat(museumSignature.factsBorder)).toBeGreaterThanOrEqual(2);
  expect(museumSignature.factLabelColor).not.toBe("rgb(0, 0, 0)");
  expect(museumSignature.photoCaption).toContain("Specimen photo 01");
  expect(museumSignature.photoCaptionFits).toBe(true);
  expect(museumSignature.photoCaptionWhiteSpace).toBe("nowrap");
  expect(Number.parseFloat(museumSignature.measurementRail)).toBeGreaterThanOrEqual(6);
  expect(Number.parseFloat(museumSignature.sealRadius)).toBeGreaterThanOrEqual(10);
  expect(Number.parseFloat(museumSignature.sealBorder)).toBeGreaterThanOrEqual(4);
  expect(museumSignature.catalogNote).toContain("Not recorded");

  if (artifactDirectory) {
    await page.setViewportSize({ width: 1280, height: 1000 });
    for (const [name, id] of certificateStyleCases) {
      await selectCertificateStyle(stylePicker, preview, name, id);
      await preview.screenshot({ path: join(artifactDirectory, `filled-${id}-1280.png`) });
    }
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await selectCertificateStyle(stylePicker, preview, "Museum Type", "museum-type");
      await preview.screenshot({ path: join(artifactDirectory, `filled-museum-type-${width}.png`) });
    }
  }
});

test("captures blank certificate style references when requested", async ({ page }) => {
  test.skip(!process.env.COA_ARTIFACT_DIR, "External visual artifacts were not requested.");
  const artifactDirectory = process.env.COA_ARTIFACT_DIR!;
  await mkdir(artifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("/#builder");
  const preview = page.locator(".certificate-preview");
  const stylePicker = page.getByRole("group", { name: "Certificate template" });
  for (const [name, id] of certificateStyleCases) {
    await selectCertificateStyle(stylePicker, preview, name, id);
    await preview.screenshot({ path: join(artifactDirectory, `blank-${id}-1280.png`) });
  }
});

test("loads the workbench on desktop and mobile without horizontal overflow", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /certificate is only as enduring/i })).toBeVisible();
  await expect(page.getByLabel("Weight (grams)")).toHaveValue("");
  await expect(page.getByLabel("Specimen form")).toHaveValue("");
  const stylePicker = page.getByRole("group", { name: "Certificate template" });
  const themePicker = page.getByRole("group", { name: "Certificate color scheme" });
  await expect(stylePicker.getByRole("radio")).toHaveCount(2);
  await expect(themePicker.getByRole("radio")).toHaveCount(9);
  await expect(stylePicker.getByRole("radio", { name: /Celestial Formal/ })).toBeChecked();
  await expect(themePicker.getByRole("radio", { name: /Observatory Navy/ })).toBeChecked();

  const proofChain = page.locator(".hero__ledger");
  const proofChainBox = await proofChain.boundingBox();
  expect(proofChainBox?.width).toBeGreaterThan(450);
  expect(Number.parseFloat(await proofChain.locator("li strong").first().evaluate((element) => getComputedStyle(element).fontSize)))
    .toBeGreaterThanOrEqual(22);

  const certificatePreview = page.locator(".certificate-preview");
  const previewViewport = page.getByLabel("Scrollable live certificate preview");
  const fallbackMark = certificatePreview.locator(".certificate-preview__collection > .orbit-mark");
  await expect(fallbackMark).toBeVisible();
  await expect(certificatePreview.locator(".certificate-preview__logo")).toHaveCount(0);

  const logoInput = page.getByLabel("Logo");
  await logoInput.setInputFiles({ name: "tiny-collection-logo.png", mimeType: "image/png", buffer: onePixelPng });
  const liveLogo = certificatePreview.locator(".certificate-preview__collection img.certificate-preview__logo");
  await expect(liveLogo).toBeVisible();
  await expect(liveLogo).toHaveAttribute("src", /^blob:/);
  await expect(liveLogo).toHaveAttribute("alt", "Collection logo");
  const firstLogoSource = await liveLogo.getAttribute("src");
  const logoGeometry = await liveLogo.evaluate((element) => {
    const image = element as HTMLImageElement;
    const style = getComputedStyle(image);
    const box = image.getBoundingClientRect();
    const frameBox = image.parentElement!.getBoundingClientRect();
    const collectionBox = image.parentElement!.parentElement!.getBoundingClientRect();
    return {
      objectFit: style.objectFit,
      objectPosition: style.objectPosition,
      width: box.width,
      height: box.height,
      naturalRatio: image.naturalWidth / image.naturalHeight,
      renderedRatio: box.width / box.height,
      frameRatio: frameBox.width / frameBox.height,
      contained: box.left >= collectionBox.left && box.right <= collectionBox.right
        && box.top >= collectionBox.top && box.bottom <= collectionBox.bottom,
    };
  });
  expect(logoGeometry.objectFit).toBe("contain");
  expect(logoGeometry.objectPosition).toBe("50% 50%");
  expect(logoGeometry.width).toBeGreaterThan(0);
  expect(logoGeometry.height).toBeGreaterThan(0);
  expect(logoGeometry.renderedRatio).toBeCloseTo(logoGeometry.naturalRatio, 3);
  expect(logoGeometry.frameRatio).toBeCloseTo(logoGeometry.naturalRatio, 1);
  expect(logoGeometry.contained).toBe(true);

  await logoInput.setInputFiles({ name: "wide-transparent-logo.svg", mimeType: "image/svg+xml", buffer: wideTransparentLogoSvg });
  await expect.poll(() => liveLogo.getAttribute("src")).not.toBe(firstLogoSource);
  await expect.poll(() => liveLogo.evaluate((element) => {
    const image = element as HTMLImageElement;
    return image.complete && image.naturalWidth > image.naturalHeight;
  })).toBe(true);

  const wideLogoSource = await liveLogo.getAttribute("src");
  await logoInput.setInputFiles({ name: "tall-transparent-logo.svg", mimeType: "image/svg+xml", buffer: tallTransparentLogoSvg });
  await expect.poll(() => liveLogo.getAttribute("src")).not.toBe(wideLogoSource);
  await expect.poll(() => liveLogo.evaluate((element) => {
    const image = element as HTMLImageElement;
    return image.complete && image.naturalHeight > image.naturalWidth;
  })).toBe(true);
  await expect(fallbackMark).toHaveCount(0);

  const styleSignatures = new Set<string>();
  for (const [name, id] of certificateStyleCases) {
    await selectCertificateStyle(stylePicker, certificatePreview, name, id);
    await expect(liveLogo).toBeVisible();
    styleSignatures.add(await certificatePreview.evaluate((element) => {
      const previewStyle = getComputedStyle(element);
      const frameStyle = getComputedStyle(element.querySelector(".certificate-preview__frame")!);
      const headerStyle = getComputedStyle(element.querySelector(".certificate-preview__header")!);
      const photoStyle = getComputedStyle(element.querySelector(".certificate-preview__photo")!);
      const factsStyle = getComputedStyle(element.querySelector(".certificate-preview__facts")!);
      const weightStyle = getComputedStyle(element.querySelector(".certificate-preview__weight")!);
      const logoStyle = getComputedStyle(element.querySelector(".certificate-preview__logo")!);
      return [
        previewStyle.padding, previewStyle.borderTopWidth, previewStyle.borderTopStyle, previewStyle.borderRadius,
        previewStyle.backgroundImage, frameStyle.borderRadius, frameStyle.boxShadow,
        headerStyle.color, headerStyle.backgroundColor, headerStyle.backgroundImage, headerStyle.borderBottomWidth,
        photoStyle.backgroundColor, photoStyle.borderTopWidth, photoStyle.borderRadius,
        factsStyle.borderTopWidth, factsStyle.borderRadius,
        weightStyle.color, weightStyle.backgroundColor, weightStyle.backgroundImage, weightStyle.borderRadius,
        logoStyle.backgroundColor, logoStyle.borderRadius,
      ].join("|");
    }));
  }
  expect(styleSignatures.size).toBe(2);

  const themeSignatures = new Set<string>();
  for (const [styleName, styleId] of certificateStyleCases) {
    await selectCertificateStyle(stylePicker, certificatePreview, styleName, styleId);
    for (const [name, id, expectedVariables] of themeCases) {
      await themePicker.getByRole("radio", { name: new RegExp(name) }).check();
      await expect(certificatePreview).toHaveAttribute("data-certificate-theme", id);
      const variables = await certificatePreview.evaluate((element) => {
        const style = getComputedStyle(element);
        return [
          "--certificate-dark",
          "--certificate-dark-soft",
          "--certificate-accent",
          "--certificate-accent-light",
          "--certificate-paper",
        ].map((property) => style.getPropertyValue(property).trim());
      });
      expect(variables).toEqual(expectedVariables);
      themeSignatures.add(variables.join("|"));
    }
  }
  expect(themeSignatures.size).toBe(9);
  await expect(certificatePreview).toHaveAttribute("data-certificate-style", "museum-type");

  const photoBox = await certificatePreview.locator(".certificate-preview__photo").boundingBox();
  const detailsBox = await certificatePreview.locator(".certificate-preview__weight").boundingBox();
  const sealBox = await certificatePreview.locator(".certificate-preview__seal").boundingBox();
  expect(photoBox && detailsBox && sealBox).toBeTruthy();
  expect(detailsBox!.y).toBeGreaterThanOrEqual(photoBox!.y + photoBox!.height);
  expect(sealBox!.y).toBeGreaterThanOrEqual(detailsBox!.y + detailsBox!.height);
  expect(sealBox!.width).toBeLessThan(detailsBox!.width / 2);
  await expect(certificatePreview.getByText("Recorded specimen")).toBeVisible();

  const desktopDimensions = await page.evaluate(() => ({
    pageWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    frame: document.querySelector<HTMLElement>(".certificate-preview__frame")!.getBoundingClientRect().toJSON(),
    body: document.querySelector<HTMLElement>(".certificate-preview__body")!.getBoundingClientRect().toJSON(),
    frameOverflow: getComputedStyle(document.querySelector<HTMLElement>(".certificate-preview__frame")!).overflow,
  }));
  expect(desktopDimensions.pageWidth).toBeLessThanOrEqual(desktopDimensions.viewportWidth);
  expect(desktopDimensions.frameOverflow).toBe("hidden");
  expect(desktopDimensions.body.left).toBeGreaterThanOrEqual(desktopDimensions.frame.left);
  expect(desktopDimensions.body.right).toBeLessThanOrEqual(desktopDimensions.frame.right);

  await expect(previewViewport).toHaveAttribute("tabindex", "0");
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.locator(".site-header")).toBeVisible();
    await expect(liveLogo).toBeVisible();
    const previewScroll = await previewViewport.evaluate((element) => ({
      clientWidth: element.clientWidth,
      overflowX: getComputedStyle(element).overflowX,
      scrollWidth: element.scrollWidth,
    }));
    expect(previewScroll.overflowX).toBe("auto");
    expect(previewScroll.scrollWidth).toBeGreaterThan(previewScroll.clientWidth);
    await previewViewport.evaluate((element) => { element.scrollLeft = 0; });
    await previewViewport.focus();
    await page.keyboard.press("ArrowRight");
    await expect.poll(() => previewViewport.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    await previewViewport.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    const rightContentVisible = await previewViewport.evaluate((element) => {
      const viewport = element.getBoundingClientRect();
      const certificateId = element.querySelector<HTMLElement>(".certificate-preview__id")!.getBoundingClientRect();
      return certificateId.left < viewport.right && certificateId.right > viewport.left;
    });
    expect(rightContentVisible).toBe(true);
    const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    expect(dimensions.width, `page overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport);
  }

  await logoInput.setInputFiles([]);
  await expect(liveLogo).toHaveCount(0);
  await expect(fallbackMark).toBeVisible();
  expect(errors).toEqual([]);
});

test("fits square, wide, and tall issuer logos at natural ratio in both responsive previews", async ({ page }) => {
  await page.goto("/#builder");
  const artifactDirectory = process.env.COA_ARTIFACT_DIR;
  if (artifactDirectory) await mkdir(artifactDirectory, { recursive: true });
  const preview = page.locator(".certificate-preview");
  const logoInput = page.getByLabel("Logo");
  const logo = preview.locator(".certificate-preview__logo");
  const stylePicker = page.getByRole("group", { name: "Certificate template" });
  const logos = [
    { name: "square-logo.png", mimeType: "image/png", buffer: solidPng(160, 160), ratio: 1 },
    { name: "wide-logo.svg", mimeType: "image/svg+xml", buffer: wideTransparentLogoSvg, ratio: 240 / 36 },
    { name: "tall-logo.svg", mimeType: "image/svg+xml", buffer: tallTransparentLogoSvg, ratio: 30 / 180 },
  ];

  for (const viewportWidth of [1280, 390]) {
    await page.setViewportSize({ width: viewportWidth, height: viewportWidth === 390 ? 844 : 1000 });
    for (const [styleName, styleId] of certificateStyleCases) {
      await selectCertificateStyle(stylePicker, preview, styleName, styleId);
      for (const logoCase of logos) {
        await logoInput.setInputFiles(logoCase);
        await expect.poll(() => logo.evaluate((element: HTMLImageElement) => (
          element.complete ? element.naturalWidth / element.naturalHeight : 0
        ))).toBeCloseTo(logoCase.ratio, 4);
        const geometry = await preview.evaluate((element) => {
          const image = element.querySelector<HTMLImageElement>(".certificate-preview__logo")!;
          const logoFrame = element.querySelector<HTMLElement>(".certificate-preview__logo-frame")!;
          const recordType = element.querySelector<HTMLElement>(".certificate-preview__record-type")!;
          const title = element.querySelector<HTMLElement>(".certificate-preview__header > strong")!;
          const id = element.querySelector<HTMLElement>(".certificate-preview__id")!;
          const canvas = element.querySelector<HTMLElement>(".certificate-preview__canvas")!;
          const imageBox = image.getBoundingClientRect();
          const frameBox = logoFrame.getBoundingClientRect();
          const frameStyle = getComputedStyle(logoFrame);
          const scale = Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--certificate-preview-scale"));
          const horizontalInset = (Number.parseFloat(frameStyle.paddingLeft) + Number.parseFloat(frameStyle.paddingRight)
            + Number.parseFloat(frameStyle.borderLeftWidth) + Number.parseFloat(frameStyle.borderRightWidth)) * scale;
          const verticalInset = (Number.parseFloat(frameStyle.paddingTop) + Number.parseFloat(frameStyle.paddingBottom)
            + Number.parseFloat(frameStyle.borderTopWidth) + Number.parseFloat(frameStyle.borderBottomWidth)) * scale;
          const toBox = (node: HTMLElement) => node.getBoundingClientRect().toJSON();
          return {
            image: imageBox.toJSON(),
            frame: frameBox.toJSON(),
            frameContentRatio: (frameBox.width - horizontalInset) / (frameBox.height - verticalInset),
            naturalRatio: image.naturalWidth / image.naturalHeight,
            canonicalArea: imageBox.width * imageBox.height / (scale * scale),
            recordType: toBox(recordType),
            title: toBox(title),
            id: toBox(id),
            pageWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
          };
        });
        const previousMaximum = styleId === "museum-type" ? { width: 56, height: 34 } : { width: 72, height: 46 };
        const previousScale = Math.min(previousMaximum.width / logoCase.ratio, previousMaximum.height);
        const previousArea = previousScale * logoCase.ratio * previousScale;
        expect(geometry.image.width / geometry.image.height, `${styleId} ${logoCase.name} image ratio`).toBeCloseTo(geometry.naturalRatio, 2);
        expect(geometry.frameContentRatio, `${styleId} ${logoCase.name} frame ratio`).toBeCloseTo(geometry.naturalRatio, 2);
        expect(geometry.canonicalArea, `${styleId} ${logoCase.name} area`).toBeGreaterThanOrEqual(previousArea * 3);
        expect(boxesIntersect(geometry.frame, geometry.recordType), `${styleId} ${logoCase.name} logo/record type ${JSON.stringify({ frame: geometry.frame, recordType: geometry.recordType })}`).toBe(false);
        expect(boxesIntersect(geometry.frame, geometry.title), `${styleId} ${logoCase.name} logo/title`).toBe(false);
        expect(boxesIntersect(geometry.frame, geometry.id), `${styleId} ${logoCase.name} logo/id`).toBe(false);
        expect(geometry.pageWidth, `${styleId} ${logoCase.name} page overflow at ${viewportWidth}px`).toBeLessThanOrEqual(geometry.viewportWidth);
        if (artifactDirectory) {
          await preview.screenshot({
            path: join(artifactDirectory, `logo-${styleId}-${logoCase.name.replace(/\.[^.]+$/, "")}-${viewportWidth}.png`),
          });
        }
      }
    }
  }
});

test("exports adaptive logos without crop or distortion in both certificate styles", async ({ page }) => {
  await page.goto("/");
  const values = {
    issuerName: "Test Issuer",
    collectionName: "Natural History Research Collection",
    issuerEmail: "",
    issuerPhone: "",
    issuerAddress: "",
    issuerWebsite: "",
    certificateId: "LOGO-TEST-0001",
    issueDate: "2026-09-05",
    certificateVersion: "1.0",
    certificateStatus: "active",
    certificateStyle: "celestial-formal",
    certificateTheme: "observatory-navy",
    supersededCertificateId: "",
    certificateNotes: "",
    meteoriteIdentity: "unclassified",
    meteoriteName: "Logo Geometry Specimen",
    meteoriteType: "Unclassified",
    classification: "Unclassified",
    meteoriteSubclass: "Unclassified",
    suspectedType: "",
    officialNameVerified: false,
    weightGrams: "12.3",
    weightPrecision: "0.1",
    specimenForm: "Fragment",
    dimensions: "",
    numberOfPieces: "1",
    preparationState: "",
    identifyingMarks: "",
    recordedOwner: "",
    fallStatus: "Find",
    fallDate: "",
    country: "Canada",
    region: "",
    locality: "",
    latitude: "",
    longitude: "",
    metbullCode: "",
    officialReferenceUrl: "",
    finderName: "",
    recoveryInformation: "",
    provenance: "",
    previousOwner: "",
    intermediaryPurchaserName: "",
    buyer: "",
    transferDate: "",
    invoiceReference: "",
    transferNotes: "",
  } satisfies FormValues;

  const results = await page.evaluate(async (baseValues) => {
    const modulePath = "/src/lib/certificate.ts";
    const { renderCertificate } = await import(/* @vite-ignore */ modulePath) as typeof import("../../src/lib/certificate");
    const variants = [[160, 160], [640, 96], [96, 640]] as const;
    const styles = ["celestial-formal", "museum-type"] as const;
    const output: Array<{
      style: string;
      sourceWidth: number;
      sourceHeight: number;
      logoDraw: { x: number; y: number; width: number; height: number };
      photoDraw: { x: number; y: number; width: number; height: number };
      titleDraw: { x: number; y: number };
      pngBytes: number;
      pdfBytes: number;
    }> = [];
    const originalDrawImage = CanvasRenderingContext2D.prototype.drawImage;
    const originalFillText = CanvasRenderingContext2D.prototype.fillText;
    let expectedLogo = { width: 0, height: 0 };
    let logoDraw: { x: number; y: number; width: number; height: number } | undefined;
    let photoDraw: { x: number; y: number; width: number; height: number } | undefined;
    let titleDraw: { x: number; y: number } | undefined;

    CanvasRenderingContext2D.prototype.drawImage = function (...args: any[]) {
      const image = args[0];
      if (image instanceof HTMLImageElement
        && image.naturalWidth === expectedLogo.width
        && image.naturalHeight === expectedLogo.height
        && args.length === 5) {
        logoDraw = { x: args[1], y: args[2], width: args[3], height: args[4] };
      }
      if (image instanceof HTMLImageElement
        && image.naturalWidth === 100
        && image.naturalHeight === 100
        && args.length === 5) {
        photoDraw = { x: args[1], y: args[2], width: args[3], height: args[4] };
      }
      return originalDrawImage.apply(this, args as any);
    };
    CanvasRenderingContext2D.prototype.fillText = function (...args) {
      if (String(args[0]).startsWith("CERTIFICATE OF AUTHENTICITY")) titleDraw = { x: args[1], y: args[2] };
      return originalFillText.apply(this, args);
    };

    try {
      const photo = new File([
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#333"/></svg>',
      ], "specimen.svg", { type: "image/svg+xml" });
      for (const style of styles) {
        for (const [sourceWidth, sourceHeight] of variants) {
          expectedLogo = { width: sourceWidth, height: sourceHeight };
          logoDraw = undefined;
          photoDraw = undefined;
          titleDraw = undefined;
          const logo = new File([
            `<svg xmlns="http://www.w3.org/2000/svg" width="${sourceWidth}" height="${sourceHeight}" viewBox="0 0 ${sourceWidth} ${sourceHeight}"><rect width="100%" height="100%" fill="none"/><path d="M0 0L${sourceWidth} ${sourceHeight}M${sourceWidth} 0L0 ${sourceHeight}" stroke="#b87518" stroke-width="8"/></svg>`,
          ], `${style}-${sourceWidth}x${sourceHeight}.svg`, { type: "image/svg+xml" });
          const rendered = await renderCertificate({
            values: { ...baseValues, certificateStyle: style },
            fingerprint: "AA:".repeat(31) + "AA",
            recordHash: "a".repeat(64),
            qrPayload: `https://example.invalid/${style}/${sourceWidth}x${sourceHeight}`,
            mainPhoto: photo,
            logo,
          });
          if (!logoDraw || !photoDraw || !titleDraw) throw new Error(`Missing export geometry for ${style} ${sourceWidth}x${sourceHeight}`);
          output.push({ style, sourceWidth, sourceHeight, logoDraw, photoDraw, titleDraw, pngBytes: rendered.png.byteLength, pdfBytes: rendered.pdf.byteLength });
        }
      }
      return output;
    } finally {
      CanvasRenderingContext2D.prototype.drawImage = originalDrawImage;
      CanvasRenderingContext2D.prototype.fillText = originalFillText;
    }
  }, values);

  expect(results).toHaveLength(6);
  for (const result of results) {
    const sourceRatio = result.sourceWidth / result.sourceHeight;
    const previousMaximum = result.style === "museum-type" ? { width: 106, height: 64 } : { width: 120, height: 105 };
    const previousScale = Math.min(previousMaximum.width / result.sourceWidth, previousMaximum.height / result.sourceHeight);
    const previousArea = result.sourceWidth * previousScale * result.sourceHeight * previousScale;
    expect(result.logoDraw.width / result.logoDraw.height, `${result.style} ${result.sourceWidth}x${result.sourceHeight} ratio`).toBeCloseTo(sourceRatio, 8);
    expect(result.photoDraw.width).toBe(455);
    expect(result.photoDraw.height).toBe(455);
    expect(result.logoDraw.width * result.logoDraw.height, `${result.style} ${result.sourceWidth}x${result.sourceHeight} area`).toBeGreaterThanOrEqual(previousArea * 3);
    if (result.style === "celestial-formal") {
      expect(result.logoDraw.x + result.logoDraw.width).toBeLessThan(result.titleDraw.x);
    } else {
      expect(result.logoDraw.y + result.logoDraw.height).toBeLessThan(result.titleDraw.y);
    }
    expect(result.pngBytes).toBeGreaterThan(10_000);
    expect(result.pdfBytes).toBeGreaterThan(10_000);
  }
});

test("scopes local processing and disabled email accurately in service policies", async ({ page }) => {
  await page.goto("/policies/privacy.html");
  await expect(page.getByText("Certificate generation and signing remain local even if you choose the paid service.")).toBeVisible();
  await expect(page.getByText(/Automated email is not currently enabled/)).toBeVisible();
  await expect(page.getByText(/browser-only certificate generator remains local unless/i)).toHaveCount(0);

  await page.goto("/policies/terms.html");
  await expect(page.getByText(/Automated email notices are not currently enabled/)).toBeVisible();
  await expect(page.getByText(/retain the private recovery code/)).toBeVisible();
  await expect(page.getByText(/Checkout displays the current managed service price before payment/)).toBeVisible();
  await expect(page.getByText(/final notice is sent/)).toHaveCount(0);
  await expect(page.getByText(/Monitoring continues through at least six confirmations/)).toBeVisible();

  await page.goto("/policies/refunds.html");
  await expect(page.getByText(/email provider delay/)).toHaveCount(0);
});

test("keeps certificate facts, signoff, and non-active status treatments disjoint", async ({ page }) => {
  await page.goto("/#builder");
  const statusSelect = page.locator('select[name="certificateStatus"]');
  await statusSelect.selectOption("revoked", { force: true });

  const certificatePreview = page.locator(".certificate-preview");
  const status = certificatePreview.locator(".certificate-preview__status");
  const stylePicker = page.getByRole("group", { name: "Certificate template" });

  for (const width of [1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });

    for (const [name, id] of certificateStyleCases) {
      await selectCertificateStyle(stylePicker, certificatePreview, name, id);
      await expect(status).toBeVisible();
      await expect(status).toHaveText("revoked");

      const frameBox = await certificatePreview.locator(".certificate-preview__frame").boundingBox();
      const factsBox = await certificatePreview.locator(".certificate-preview__facts").boundingBox();
      const signoffBox = await certificatePreview.locator(".certificate-preview__signoff").boundingBox();
      const statusBox = await status.boundingBox();
      expect(frameBox && factsBox && signoffBox && statusBox, `${id} boxes at ${width}px`).toBeTruthy();
      expect(
        boxesIntersect(factsBox!, signoffBox!),
        `${id} facts/signoff at ${width}px: ${JSON.stringify({ factsBox, signoffBox })}`,
      ).toBe(false);

      for (const selector of [
        ".certificate-preview__title",
        ".certificate-preview__facts",
        ".certificate-preview__photo",
        ".certificate-preview__weight",
        ".certificate-preview__signoff",
        ".certificate-preview__seal",
      ]) {
        const contentBox = await certificatePreview.locator(selector).boundingBox();
        expect(contentBox, `${id} ${selector} box at ${width}px`).toBeTruthy();
        expect(boxesIntersect(statusBox!, contentBox!), `${id} status/${selector} at ${width}px`).toBe(false);
      }

      expect(statusBox!.x).toBeGreaterThanOrEqual(frameBox!.x);
      expect(statusBox!.y).toBeGreaterThanOrEqual(frameBox!.y);
      expect(statusBox!.x + statusBox!.width).toBeLessThanOrEqual(frameBox!.x + frameBox!.width);
      expect(statusBox!.y + statusBox!.height).toBeLessThanOrEqual(frameBox!.y + frameBox!.height);
      expect(statusBox!.width).toBeGreaterThan(30);
      const statusType = await status.evaluate((element) => {
        const canvas = element.closest(".certificate-preview")!.querySelector<HTMLElement>(".certificate-preview__canvas")!;
        const scale = Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--certificate-preview-scale"));
        return Number.parseFloat(getComputedStyle(element).fontSize) * scale;
      });
      expect(statusType, `${id} rendered status font at ${width}px`).toBeGreaterThanOrEqual(12);

      const dimensions = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
      }));
      expect(dimensions.width, `${id} horizontal overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport);
    }
  }

  for (const value of ["revoked", "transferred", "superseded"]) {
    await statusSelect.selectOption(value, { force: true });
    await expect(status).toHaveText(value);
    const textDimensions = await status.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(
      textDimensions.scrollWidth <= textDimensions.clientWidth,
      `${value} status text fit at 320px: ${JSON.stringify(textDimensions)}`,
    ).toBe(true);
  }
});

test("keeps unbroken Museum Type export notes inside their ruled panel", async ({ page }) => {
  await page.goto("/");
  const values = {
    issuerName: "Test Issuer",
    collectionName: "Test Meteorite Collection",
    issuerEmail: "",
    issuerPhone: "",
    issuerAddress: "",
    issuerWebsite: "",
    certificateId: "WRAP-TEST-0001",
    issueDate: "2026-07-29",
    certificateVersion: "1.0",
    certificateStatus: "active",
    certificateStyle: "museum-type",
    certificateTheme: "observatory-navy",
    supersededCertificateId: "",
    certificateNotes: "",
    meteoriteIdentity: "unclassified",
    meteoriteName: "Test Meteorite",
    meteoriteType: "Unclassified",
    classification: "Unclassified",
    meteoriteSubclass: "Unclassified",
    suspectedType: "Possible L5 chondrite",
    officialNameVerified: false,
    weightGrams: "12.3",
    weightPrecision: "0.1",
    specimenForm: "Fragment",
    dimensions: "",
    numberOfPieces: "1",
    preparationState: "",
    identifyingMarks: "",
    fallStatus: "Find",
    fallDate: "2024-01-15",
    country: "Canada",
    region: "",
    locality: "Example Township",
    latitude: "45.4215 N",
    longitude: "75.6972 W",
    metbullCode: "",
    officialReferenceUrl: "",
    finderName: "",
    recoveryInformation: "R".repeat(3000),
    provenance: "P".repeat(5000),
    previousOwner: "",
    intermediaryPurchaserName: "",
    buyer: "",
    transferDate: "",
    invoiceReference: "",
    transferNotes: "",
  } satisfies FormValues;

  const noteDraws = await page.evaluate(async (renderValues) => {
    const modulePath = "/src/lib/certificate.ts";
    const { renderCertificate } = await import(/* @vite-ignore */ modulePath) as typeof import("../../src/lib/certificate");
    const draws: Array<{ text: string; width: number; y: number; fontSize: number }> = [];
    const originalFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (...args) {
      const [text, x, y] = args;
      if (x === 134 && y >= 1332 && y <= 1496) {
        draws.push({
          text: String(text),
          width: this.measureText(String(text)).width,
          y,
          fontSize: Number.parseFloat(this.font.match(/([0-9.]+)px/)?.[1] ?? "0"),
        });
      }
      return originalFillText.apply(this, args);
    };

    try {
      const photo = new File([
        '<svg xmlns="http://www.w3.org/2000/svg" width="560" height="455"><rect width="560" height="455" fill="#333"/></svg>',
      ], "specimen.svg", { type: "image/svg+xml" });
      await renderCertificate({
        values: renderValues,
        fingerprint: "AA:".repeat(31) + "AA",
        recordHash: "a".repeat(64),
        qrPayload: "https://example.invalid/verify/WRAP-TEST-0001",
        mainPhoto: photo,
      });
      return draws;
    } finally {
      CanvasRenderingContext2D.prototype.fillText = originalFillText;
    }
  }, values);

  const provenanceDraws = noteDraws.filter(({ y }) => y <= 1419);
  const supplementalDraws = noteDraws.filter(({ y }) => y >= 1472);
  expect(provenanceDraws).toHaveLength(4);
  expect(supplementalDraws).toHaveLength(2);
  expect(provenanceDraws.at(-1)?.text).toMatch(/\.\.\.$/);
  expect(supplementalDraws.at(-1)?.text).toMatch(/\.\.\.$/);
  for (const draw of noteDraws) {
    expect(draw.width, `note line at y=${draw.y}: ${draw.text}`).toBeLessThanOrEqual(922);
    expect(draw.fontSize).toBeGreaterThanOrEqual(CERTIFICATE_EXPORT_FONT_FLOOR);
  }
});

test("issues and verifies a minimal package without optional PII", async ({ page }) => {
  const mutatingRequests: string[] = [];
  page.on("request", (request) => {
    if (!["GET", "HEAD"].includes(request.method())) mutatingRequests.push(`${request.method()} ${request.url()}`);
  });
  await page.goto("/#builder");

  await page.locator('input[name="issuerName"]').fill("Minimal Test Issuer");
  await page.locator('input[name="collectionName"]').fill("Minimal Test Collection");
  await page.locator('input[name="certificateId"]').fill("MINIMAL-COA-0001");
  await page.locator('input[name="issueDate"]').fill("2026-07-30");
  await page.locator('input[name="certificateVersion"]').fill("1.0");
  await page.locator('input[name="meteoriteName"]').fill("Minimal Meteorite");
  await page.locator('input[name="weightGrams"]').fill("12.3");
  await page.locator('input[name="weightPrecision"]').fill("0.1");
  await page.locator('select[name="specimenForm"]').selectOption({ label: "Fragment" });
  await page.locator('input[name="numberOfPieces"]').fill("1");
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.locator('input[name="fallStatus"]').fill("Find");
  await page.locator('input[name="country"]').fill("Canada");
  await page.locator('input[name="locality"]').fill("Example Township");
  await page.getByRole("radio", { name: /Official/ }).check();
  await page.locator('input[name="meteoriteType"]').fill("Stale type");
  await page.locator('input[name="classification"]').fill("Stale class");
  await page.locator('input[name="meteoriteSubclass"]').fill("Stale subclass");
  await page.locator('input[name="officialReferenceUrl"]').fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=99999");
  await page.locator('input[name="officialNameVerified"]').check();
  await page.getByRole("radio", { name: /Unclassified/ }).check();
  await page.locator('input[name="suspectedType"]').fill("Possible L5 chondrite");
  const issueButton = page.getByRole("button", { name: "Issue cryptographically signed COA package" });
  await expect(issueButton).toBeDisabled();
  await expect(page.getByText("Generate or import a signing identity.")).toBeVisible();

  const createKey = page.locator(".key-option").first();
  await createKey.getByLabel("Passphrase", { exact: true }).fill("minimal package passphrase");
  await createKey.getByLabel("Confirm passphrase").fill("minimal package passphrase");
  await createKey.getByRole("button", { name: "Generate Ed25519 key" }).click();
  await expect(page.getByText("Key loaded", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(issueButton).toBeDisabled();
  await expect(page.getByText("Download the encrypted signing-key backup.")).toBeVisible();
  const backupDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download encrypted key backup" }).click();
  await backupDownload;
  await expect(issueButton).toBeDisabled();
  await expect(page.getByText("Add at least one source-original specimen photograph.")).toBeVisible();

  await page.locator(".photo-drop input[type=file]").setInputFiles({
    name: "minimal-exact-specimen.png",
    mimeType: "image/png",
    buffer: certificatePhotoPng,
  });
  await expect(issueButton).toBeDisabled();
  await expect(page.getByText("Attest every source photograph is an unmodified original.")).toBeVisible();
  await page.getByLabel(/I attest that this source file is an exact/).check();
  await expect(issueButton).toBeEnabled();

  const packageDownload = page.waitForEvent("download", { timeout: 90_000 });
  await issueButton.click();
  const packagePath = await (await packageDownload).path();
  expect(packagePath).toBeTruthy();
  expect(mutatingRequests).toEqual([]);

  const archive = await JSZip.loadAsync(await readFile(packagePath!));
  const manifestName = Object.keys(archive.files).find((name) => name.endsWith("/manifest.json"));
  expect(manifestName).toBeTruthy();
  const root = manifestName!.slice(0, -"manifest.json".length);
  const manifest = JSON.parse(await archive.file(manifestName!)!.async("text")) as Record<string, any>;
  const record = JSON.parse(await archive.file(`${root}certificate-record.json`)!.async("text")) as Record<string, any>;

  expect(manifest).toEqual(expect.objectContaining({
    $schema: "coa-manifest-v2.schema.json",
    schemaVersion: "2.2.0",
    packageVersion: 2,
  }));
  expect(manifest.issuer).toEqual(expect.objectContaining({
    name: "Minimal Test Issuer",
    collection: "Minimal Test Collection",
  }));
  for (const key of ["email", "phone", "address", "website", "logoFile"]) expect(manifest.issuer).not.toHaveProperty(key);
  for (const key of ["notes", "supersedes"]) expect(manifest.certificate).not.toHaveProperty(key);
  for (const key of ["dimensions", "preparationState", "identifyingMarks"]) {
    expect(manifest.specimen).not.toHaveProperty(key);
  }
  expect(manifest.specimen.recordedOwner).toBe("Minimal Test Issuer");
  expect(manifest.specimen).toEqual(expect.objectContaining({
    meteoriteIdentity: "unclassified",
    meteoriteType: "Unclassified",
    classification: "Unclassified",
    meteoriteSubclass: "Unclassified",
    suspectedType: "Possible L5 chondrite",
  }));
  expect(manifest.specimen).not.toHaveProperty("officialNameVerified");
  expect(manifest.specimen.fall).toEqual({ status: "Find", country: "Canada", locality: "Example Township" });
  expect(manifest.specimen.fall).not.toHaveProperty("metbullCode");
  expect(manifest.specimen.fall).not.toHaveProperty("officialReferenceUrl");
  expect(manifest.specimen.provenance).toEqual({});
  expect(manifest.photographs[0]).not.toHaveProperty("caption");
  expect(manifest.photographs[0]).not.toHaveProperty("captureDate");
  expect(record.issuer).toEqual(manifest.issuer);
  expect(record.specimen).toEqual(manifest.specimen);
  expect(record.photographs).toEqual(manifest.photographs);

  const certificateText = await archive.file(`${root}certificate.txt`)!.async("text");
  expect(certificateText).toContain("Current owner: Minimal Test Issuer");
  expect(certificateText).toContain("Date: Not recorded");
  expect(certificateText).toContain("Region: Not recorded");
  expect(certificateText).toContain("Coordinates: Not recorded");
  expect(certificateText).toContain("PROVENANCE\nNot recorded");
  expect(certificateText).not.toContain("99999");
  expect(certificateText).not.toContain("Official reference:");
  expect(certificateText).not.toContain("Official name verified:");

  await page.locator(".verifier input[type=file]").setInputFiles(packagePath!);
  await expect(page.locator(".check", { hasText: "Manifest schema" })).toHaveClass(/check--pass/, { timeout: 60_000 });
  await expect(page.locator(".check", { hasText: "Official meteorite identity" })).toHaveClass(/check--pass/);
  await expect(page.locator(".verifier__report-head").getByText("PASS")).toBeVisible();
});

test("generates, downloads, verifies, and rejects tampering", async ({ page }, testInfo) => {
  await page.goto("/#builder");

  await page.locator('input[name="issuerName"]').fill("Test Issuer");
  await page.locator('input[name="collectionName"]').fill("Test Meteorite Collection");
  await page.locator('input[name="issuerEmail"]').fill("issuer@example.com");
  await page.locator('input[name="issuerPhone"]').fill("+1 555 010 0123");
  await page.locator('input[name="issuerAddress"]').fill("123 Example Street, Ottawa, Canada");
  await page.locator('input[name="issuerWebsite"]').fill("https://example.com");
  await page.locator('input[name="certificateId"]').fill("TEST-COA-0001");
  await page.locator('input[name="issueDate"]').fill("2026-07-29");
  await page.locator('input[name="certificateVersion"]').fill("1.0");
  await page.locator('input[name="certificateNotes"]').fill("Complete package compatibility test.");
  await page.getByRole("radio", { name: /Official/ }).evaluate((element: HTMLInputElement) => element.click());
  await page.locator('input[name="meteoriteName"]').fill("Test Meteorite");
  await page.locator('input[name="classification"]').fill("Ordinary chondrite");
  await page.locator('input[name="weightGrams"]').fill("12.3");
  await page.locator('input[name="weightPrecision"]').fill("0.1");
  await page.locator('select[name="specimenForm"]').selectOption({ label: "Fragment" });
  await page.locator('input[name="dimensions"]').fill("20 x 15 x 10 mm");
  await page.locator('input[name="numberOfPieces"]').fill("1");
  await page.locator('input[name="preparationState"]').fill("Natural crust with one cut face");
  await page.locator('input[name="identifyingMarks"]').fill("Test collection label 42");
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.locator('input[name="fallStatus"]').fill("Find");
  await page.locator('input[name="fallDate"]').fill("2024-01-15");
  await page.locator('input[name="country"]').fill("Canada");
  await page.locator('input[name="region"]').fill("Ontario");
  await page.locator('input[name="locality"]').fill("Example Township");
  await page.locator('input[name="latitude"]').fill("45.4215 N");
  await page.locator('input[name="longitude"]').fill("75.6972 W");
  await page.locator('input[name="officialReferenceUrl"]').fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=12345");
  await page.getByLabel("Missing MetBull classification details").check();
  await expect(page.locator('input[name="meteoriteType"]')).toHaveValue("");
  await expect(page.locator('input[name="meteoriteSubclass"]')).toHaveValue("");
  await page.locator('input[name="officialNameVerified"]').check();
  await page.locator('input[name="finderName"]').fill("Documented Finder");
  await page.locator('textarea[name="recoveryInformation"]').fill("Recovered by the documented finder.");
  await page.locator('textarea[name="provenance"]').fill("Documented test custody from recovery through issuance.");
  await page.locator('input[name="previousOwner"]').fill("Previous Test Owner");
  await page.locator('input[name="intermediaryPurchaserName"]').fill("Intermediary Test Dealer");
  await page.locator('input[name="buyer"]').fill("Receiving Test Collector");
  await page.locator('input[name="transferDate"]').fill("2026-07-28");
  await page.locator('input[name="invoiceReference"]').fill("INV-2026-0001");
  await page.locator('textarea[name="transferNotes"]').fill("Transferred with supporting records.");

  await page.getByRole("radio", { name: /Royal Amethyst/ }).evaluate((element: HTMLInputElement) => element.click());
  await page.getByRole("radio", { name: /Museum Type/ }).evaluate((element: HTMLInputElement) => element.click());
  if (process.env.COA_ARTIFACT_DIR) {
    await page.locator('select[name="certificateStatus"]').selectOption("revoked");
  }

  const createKey = page.locator(".key-option").first();
  await createKey.getByLabel("Passphrase", { exact: true }).fill("correct horse battery staple");
  await createKey.getByLabel("Confirm passphrase").fill("correct horse battery staple");
  await createKey.getByRole("button", { name: "Generate Ed25519 key" }).click();
  await expect(page.getByText("Key loaded", { exact: true })).toBeVisible({ timeout: 30_000 });

  const backupDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download encrypted key backup" }).click();
  await backupDownload;

  await page.locator(".photo-drop input[type=file]").setInputFiles({
    name: "exact-specimen.png",
    mimeType: "image/png",
    buffer: certificatePhotoPng,
  });
  await page.getByLabel("Caption (optional)", { exact: true }).fill("Front face");
  await page.getByLabel("Capture date (optional)", { exact: true }).fill("2026-07-29");
  await page.getByLabel(/I attest that this source file is an exact/).check();
  const issueButton = page.getByRole("button", { name: "Issue cryptographically signed COA package" });
  await expect(issueButton).toBeEnabled();
  await page.locator('input[name="meteoriteName"]').fill("Test Meteorite revised");
  await expect(page.locator('input[name="officialNameVerified"]')).not.toBeChecked();
  await expect(issueButton).toBeDisabled();
  await page.locator('input[name="officialNameVerified"]').check();
  await expect(issueButton).toBeEnabled();
  await page.getByLabel("Logo (optional)").setInputFiles({
    name: "issuer-logo.png",
    mimeType: "image/png",
    buffer: onePixelPng,
  });
  const removeBox = await page.getByRole("button", { name: "Remove exact-specimen.png" }).boundingBox();
  const metadataBox = await page.locator(".photo-item__meta").boundingBox();
  expect(removeBox && metadataBox).toBeTruthy();
  const overlaps = Boolean(
    removeBox && metadataBox
    && removeBox.x < metadataBox.x + metadataBox.width
    && removeBox.x + removeBox.width > metadataBox.x
    && removeBox.y < metadataBox.y + metadataBox.height
    && removeBox.y + removeBox.height > metadataBox.y,
  );
  expect(overlaps).toBe(false);

  const longExportCollection = "Natural History Research Collection ".repeat(12).trim();
  await page.locator('input[name="collectionName"]').fill(longExportCollection);
  await page.evaluate(() => {
    const captureWindow = window as typeof window & {
      __coaHeaderDraws?: Array<{ text: string; width: number }>;
      __coaMinimumDrawnFontSize?: number;
    };
    captureWindow.__coaHeaderDraws = [];
    captureWindow.__coaMinimumDrawnFontSize = Number.POSITIVE_INFINITY;
    const originalFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (...args) {
      const [text, x, y] = args;
      const fontSize = this.font.match(/([0-9.]+)px/)?.[1];
      if (fontSize) {
        captureWindow.__coaMinimumDrawnFontSize = Math.min(
          captureWindow.__coaMinimumDrawnFontSize!,
          Number.parseFloat(fontSize),
        );
      }
      if (x >= 255 && x < 600 && y === 134) {
        captureWindow.__coaHeaderDraws!.push({ text: String(text), width: this.measureText(String(text)).width });
      }
      return originalFillText.apply(this, args);
    };
  });

  const packageDownload = page.waitForEvent("download", { timeout: 90_000 });
  await issueButton.click();
  const download = await packageDownload;
  const packagePath = await download.path();
  expect(packagePath).toBeTruthy();
  expect(download.suggestedFilename()).toContain("TEST-COA-0001");
  await expect(page.getByText("Release created")).toBeVisible();
  const collectionHeaderDraw = await page.evaluate(() => {
    const captureWindow = window as typeof window & {
      __coaHeaderDraws?: Array<{ text: string; width: number }>;
      __coaMinimumDrawnFontSize?: number;
    };
    return {
      collection: captureWindow.__coaHeaderDraws?.at(-1),
      minimumFontSize: captureWindow.__coaMinimumDrawnFontSize,
    };
  });
  expect(collectionHeaderDraw.collection).toBeTruthy();
  expect(collectionHeaderDraw.collection!.text).toMatch(/\.\.\.$/);
  expect(collectionHeaderDraw.collection!.width).toBeLessThanOrEqual(1050);
  expect(collectionHeaderDraw.minimumFontSize).toBeGreaterThanOrEqual(CERTIFICATE_EXPORT_FONT_FLOOR);

  const packageBuffer = await readFile(packagePath!);
  const archive = await JSZip.loadAsync(packageBuffer);
  const manifestName = Object.keys(archive.files).find((name) => name.endsWith("/manifest.json"));
  expect(manifestName).toBeTruthy();
  const root = manifestName!.slice(0, -"manifest.json".length);
  const manifest = JSON.parse(await archive.file(manifestName!)!.async("text")) as {
    certificate: { visualStyle?: string; visualTheme?: string };
    issuer: { email?: string; phone?: string; address?: string; website?: string; logoFile?: string };
    specimen: {
      meteoriteIdentity: string;
      meteoriteType: string;
      classification: string;
      meteoriteSubclass: string;
      officialNameVerified?: boolean;
      recordedOwner?: string;
      fall: { date?: string; latitude?: string; longitude?: string; finderName?: string };
      provenance: { statement?: string; intermediaryPurchaserName?: string };
    };
    photographs: Array<{ path: string; sha256: string; caption?: string; captureDate?: string; pixelWidth?: number; pixelHeight?: number; displayCrop?: Record<string, unknown> }>;
    files: Array<{ path: string; role: string; sha256: string }>;
  };
  expect(manifest.certificate.visualStyle).toBe("museum-type");
  expect(manifest).toEqual(expect.objectContaining({
    $schema: "coa-manifest-v2.schema.json",
    schemaVersion: "2.2.0",
    packageVersion: 2,
  }));
  expect(manifest.certificate.visualTheme).toBe("royal-amethyst");
  expect(manifest.issuer).toEqual(expect.objectContaining({
    email: "issuer@example.com",
    phone: "+1 555 010 0123",
    address: "123 Example Street, Ottawa, Canada",
    website: "https://example.com",
    logoFile: "issuer-assets/issuer-logo.png",
  }));
  expect(manifest.specimen.recordedOwner).toBe("Test Issuer");
  expect(manifest.specimen).toEqual(expect.objectContaining({
    meteoriteIdentity: "official",
    meteoriteType: "Not provided by Meteoritical Bulletin",
    classification: "Ordinary chondrite",
    meteoriteSubclass: "Not provided by Meteoritical Bulletin",
    officialNameVerified: true,
  }));
  expect(manifest.specimen.fall).toEqual(expect.objectContaining({
    date: "2024-01-15",
    latitude: "45.4215 N",
    longitude: "75.6972 W",
    finderName: "Documented Finder",
  }));
  expect(manifest.specimen.provenance.statement).toBe("Documented test custody from recovery through issuance.");
  expect(manifest.specimen.provenance.intermediaryPurchaserName).toBe("Intermediary Test Dealer");
  expect(manifest.photographs[0]).toEqual(expect.objectContaining({ caption: "Front face", captureDate: "2026-07-29" }));
  expect(manifest.photographs[0]).toEqual(expect.objectContaining({
    pixelWidth: 560,
    pixelHeight: 455,
  }));
  expect(manifest.photographs[0]).not.toHaveProperty("displayCrop");
  const originalPhoto = await archive.file(`${root}${manifest.photographs[0].path}`)!.async("nodebuffer");
  expect(originalPhoto).toEqual(certificatePhotoPng);
  expect(manifest.photographs[0].sha256).toBe(createHash("sha256").update(certificatePhotoPng).digest("hex"));
  expect(manifest.files.find((entry) => entry.path === manifest.photographs[0].path)?.role)
    .toBe("exact original specimen photograph");
  const originalLogo = await archive.file(`${root}${manifest.issuer.logoFile}`)!.async("nodebuffer");
  expect(originalLogo).toEqual(onePixelPng);
  expect(manifest.files.find((entry) => entry.path === manifest.issuer.logoFile)?.sha256)
    .toBe(createHash("sha256").update(onePixelPng).digest("hex"));
  const signedPaths = manifest.files.map((entry) => entry.path);
  expect(signedPaths).toEqual(expect.arrayContaining([
    "README-FIRST.txt",
    "coa-manifest-v2.schema.json",
    "public-key.pem",
    "verification-instructions.txt",
    "verify.py",
  ]));
  await writeFile(
    testInfo.outputPath("generated-certificate.png"),
    await archive.file(`${root}certificate.png`)!.async("nodebuffer"),
  );
  if (process.env.COA_ARTIFACT_DIR) {
    await mkdir(process.env.COA_ARTIFACT_DIR, { recursive: true });
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-museum-type-certificate.png"),
      await archive.file(`${root}certificate.png`)!.async("nodebuffer"),
    );
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-museum-type-certificate.pdf"),
      await archive.file(`${root}certificate.pdf`)!.async("nodebuffer"),
    );

    await page.getByRole("radio", { name: /Celestial Formal/ }).check();
    await expect(page.locator(".certificate-preview")).toHaveAttribute("data-certificate-style", "celestial-formal");
    const celestialPackageDownload = page.waitForEvent("download", { timeout: 90_000 });
    await page.getByRole("button", { name: "Issue cryptographically signed COA package" }).click();
    const celestialDownload = await celestialPackageDownload;
    const celestialPackagePath = await celestialDownload.path();
    expect(celestialPackagePath).toBeTruthy();
    const celestialArchive = await JSZip.loadAsync(await readFile(celestialPackagePath!));
    const celestialManifestName = Object.keys(celestialArchive.files).find((name) => name.endsWith("/manifest.json"));
    expect(celestialManifestName).toBeTruthy();
    const celestialRoot = celestialManifestName!.slice(0, -"manifest.json".length);
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-celestial-formal-certificate.png"),
      await celestialArchive.file(`${celestialRoot}certificate.png`)!.async("nodebuffer"),
    );
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-celestial-formal-certificate.pdf"),
      await celestialArchive.file(`${celestialRoot}certificate.pdf`)!.async("nodebuffer"),
    );
  }
  const certificateRecord = JSON.parse(await archive.file(`${root}certificate-record.json`)!.async("text")) as {
    schemaVersion?: string;
    certificate: { visualStyle?: string; visualTheme?: string };
    specimen: unknown;
  };
  expect(certificateRecord.schemaVersion).toBe("2.2.0");
  expect(certificateRecord.certificate.visualStyle).toBe("museum-type");
  expect(certificateRecord.certificate.visualTheme).toBe("royal-amethyst");
  expect(certificateRecord.specimen).toEqual(manifest.specimen);
  const certificateText = await archive.file(`${root}certificate.txt`)!.async("text");
  expect(certificateText).toContain("Certificate layout style: Museum Type");
  expect(certificateText).toContain("Certificate color scheme: Royal Amethyst");
  expect(certificateText).toContain("Official name verified: Yes - issuer attestation");
  expect(certificateText).toContain("missing type/subclass attested from linked MetBull entry");
  expect(certificateText).toContain("Meteorite type: Not provided by Meteoritical Bulletin");
  expect(certificateText).toContain("Official reference: https://www.lpi.usra.edu/meteor/metbull.cfm?code=12345");
  expect(certificateText).toContain("centers and contains the complete image without cropping, stretching, or distortion");
  const packagedSchema = JSON.parse(await archive.file(`${root}coa-manifest-v2.schema.json`)!.async("text")) as {
    properties: {
      certificate: {
        required: string[];
        properties: { visualStyle: { enum: string[] } };
      };
    };
  };
  expect(packagedSchema.properties.certificate.required).not.toContain("visualStyle");
  expect(packagedSchema.properties.certificate.properties.visualStyle.enum).toEqual([
    "regal-archive",
    "museum-ledger",
    "celestial-formal",
    "museum-type",
  ]);
  const verifySource = await archive.file(`${root}verify.py`)!.async("text");
  const verifyPath = testInfo.outputPath("verify.py");
  await writeFile(verifyPath, verifySource, "utf8");
  execFileSync("python3", [
    "-c",
    "import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), sys.argv[1], 'exec')",
    verifyPath,
  ]);
  if (spawnSync("python3", ["-c", "import cryptography"]).status === 0) {
    const extractedRoot = testInfo.outputPath("offline-package");
    await mkdir(extractedRoot, { recursive: true });
    for (const [name, entry] of Object.entries(archive.files)) {
      if (entry.dir || !name.startsWith(root)) continue;
      const outputPath = join(extractedRoot, name.slice(root.length));
      await mkdir(dirname(outputPath), { recursive: true });
      await writeFile(outputPath, await entry.async("nodebuffer"));
    }
    const verifierOutput = execFileSync("python3", ["verify.py"], {
      cwd: extractedRoot,
      encoding: "utf8",
    });
    expect(verifierOutput).toContain("PACKAGE VERIFICATION PASSED");
    expect(verifierOutput).toContain("OK    photograph metadata (2.2 no-crop dimensions)");

    const offlineManifestPath = join(extractedRoot, "manifest.json");
    const offlineManifest = JSON.parse(await readFile(offlineManifestPath, "utf8"));
    offlineManifest.photographs[0].displayCrop = null;
    await writeFile(offlineManifestPath, JSON.stringify(offlineManifest));
    const tamperedOffline = spawnSync("python3", ["verify.py"], { cwd: extractedRoot, encoding: "utf8" });
    expect(tamperedOffline.status).toBe(1);
    expect(tamperedOffline.stdout).toContain("FAIL  photograph metadata");
  }

  await page.locator("#verify").scrollIntoViewIfNeeded();
  await page.locator(".verifier input[type=file]").setInputFiles(packagePath!);
  await expect(page.locator(".verifier__report-head").getByText("PASS")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("All required cryptographic checks passed.")).toBeVisible();
  await expect(page.locator(".check", { hasText: "Official meteorite identity" })).toHaveClass(/check--pass/);
  await expect(page.locator(".check", { hasText: "Photograph metadata" })).toHaveClass(/check--pass/);

  const metadataArchive = await JSZip.loadAsync(packageBuffer);
  const metadataManifest = JSON.parse(await metadataArchive.file(`${root}manifest.json`)!.async("text"));
  metadataManifest.photographs[0].displayCrop = null;
  metadataManifest.photographs[0].bytes += 1;
  metadataArchive.file(`${root}manifest.json`, JSON.stringify(metadataManifest));
  await page.locator(".verifier input[type=file]").setInputFiles({
    name: "tampered-photo-metadata.zip",
    mimeType: "application/zip",
    buffer: await metadataArchive.generateAsync({ type: "nodebuffer" }),
  });
  await expect(page.locator(".check", { hasText: "Photograph metadata" })).toHaveClass(/check--fail/, { timeout: 60_000 });

  const tamperedArchive = await JSZip.loadAsync(packageBuffer);
  const tamperedVerify = `${verifySource}\n# unauthorized change\n`;
  tamperedArchive.file(`${root}verify.py`, tamperedVerify);
  const checksumName = `${root}sha256sums.txt`;
  const checksumText = await tamperedArchive.file(checksumName)!.async("text");
  const tamperedHash = createHash("sha256").update(tamperedVerify).digest("hex");
  tamperedArchive.file(
    checksumName,
    checksumText.replace(/^[a-f0-9]{64}  verify\.py$/m, `${tamperedHash}  verify.py`),
  );
  const tamperedBuffer = await tamperedArchive.generateAsync({ type: "nodebuffer" });
  await page.locator(".verifier input[type=file]").setInputFiles({
    name: "tampered-support-file.zip",
    mimeType: "application/zip",
    buffer: tamperedBuffer,
  });
  await expect(page.locator(".verifier__report-head").getByText("FAIL")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".check", { hasText: "Signed evidence files" })).toHaveClass(/check--fail/);

  const injectedArchive = await JSZip.loadAsync(packageBuffer);
  injectedArchive.file(`${root}unsigned-injected-file.txt`, "not authorized by the signed inventory\n");
  const injectedBuffer = await injectedArchive.generateAsync({ type: "nodebuffer" });
  await page.locator(".verifier input[type=file]").setInputFiles({
    name: "injected-file.zip",
    mimeType: "application/zip",
    buffer: injectedBuffer,
  });
  await expect(page.locator(".verifier__report-head").getByText("FAIL")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".check", { hasText: "Package inventory" })).toHaveClass(/check--fail/);

  const collisionArchive = await JSZip.loadAsync(packageBuffer);
  collisionArchive.file("README-FIRST.txt", "malicious out-of-root collision\n");
  const collisionBuffer = await collisionArchive.generateAsync({ type: "nodebuffer" });
  await page.locator(".verifier input[type=file]").setInputFiles({
    name: "out-of-root-collision.zip",
    mimeType: "application/zip",
    buffer: collisionBuffer,
  });
  await expect(page.locator(".verifier__report-head").getByText("FAIL")).toBeVisible({ timeout: 60_000 });
  const inventoryCheck = page.locator(".check", { hasText: "Package inventory" });
  await expect(inventoryCheck).toHaveClass(/check--fail/);
  await expect(inventoryCheck).toContainText("Outside root: README-FIRST.txt");

  const duplicateBuffer = duplicateCentralDirectoryEntry(packageBuffer, "/README-FIRST.txt");
  await page.locator(".verifier input[type=file]").setInputFiles({
    name: "duplicate-central-directory-entry.zip",
    mimeType: "application/zip",
    buffer: duplicateBuffer,
  });
  await expect(page.getByText(/The ZIP contains a duplicate entry:/)).toBeVisible({ timeout: 60_000 });
});
