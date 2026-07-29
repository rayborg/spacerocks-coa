import { expect, test } from "@playwright/test";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import JSZip from "jszip";

const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

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
  "classification",
  "weightGrams",
  "weightPrecision",
  "dimensions",
  "numberOfPieces",
  "preparationState",
  "identifyingMarks",
  "recordedOwner",
  "fallStatus",
  "fallDate",
  "country",
  "region",
  "locality",
  "latitude",
  "longitude",
  "metbullCode",
  "officialReferenceUrl",
  "recoveryInformation",
  "provenance",
  "previousOwner",
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
  await expect(page.getByRole("radio", { name: /Regal Archive/ })).toBeChecked();
  await expect(page.getByRole("radio", { name: /Observatory Navy/ })).toBeChecked();

  await expect(page.getByLabel("Issuer display or legal name")).toHaveAttribute("placeholder", "e.g., John Doe");
  await expect(page.getByLabel("Meteorite name")).toHaveAttribute("placeholder", "e.g., Aguas Zarcas");
  await expect(page.getByLabel("Weight (grams)")).toHaveAttribute("placeholder", "e.g., 44.7");
  await expect(page.getByLabel("Issuer display or legal name")).toHaveValue("");
  await expect(page.getByLabel("Meteorite name")).toHaveValue("");
  await expect(page.getByLabel("Weight (grams)")).toHaveValue("");

  const preview = page.locator(".certificate-preview");
  await expect(preview.locator(".certificate-preview__collection")).toContainText("Collection name");
  await expect(preview.locator(".certificate-preview__title h3")).toHaveText("Meteorite name");
  await expect(preview.locator(".certificate-preview__title p")).toHaveText("Classification");
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

  await page.getByRole("button", { name: "Issue signed COA package" }).click();
  await expect(page.locator(".generation-status")).toHaveText("Review the highlighted required fields.");
  await expect(page.getByLabel("Issuer display or legal name")).toHaveValue("");

  await page.locator(".photo-drop input[type=file]").setInputFiles({
    name: "filename-must-not-become-caption.png",
    mimeType: "image/png",
    buffer: onePixelPng,
    lastModified: Date.UTC(2024, 0, 15),
  });
  const photoCaption = page.getByLabel("Caption", { exact: true });
  const photoCaptureDate = page.getByLabel("Capture date", { exact: true });
  await expect(page.locator(".photo-item__meta strong")).toHaveText("filename-must-not-become-caption.png");
  await expect(photoCaption).toHaveValue("");
  await expect(photoCaptureDate).toHaveValue("");
  await expect(photoCaption).toHaveAttribute("placeholder", "e.g., Front face");
  await expect(photoCaptureDate).toHaveAttribute("placeholder", "e.g., 2026-07-29");

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
          fontSize(".preview-column__head > small"),
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
  }
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
  const stylePicker = page.getByRole("group", { name: "Certificate layout style" });

  await expect(summary).toHaveClass(/certificate-preview__weight--empty/);
  for (const width of [2048, 1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    for (const name of ["Regal Archive", "Museum Ledger", "Celestial Formal"]) {
      await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
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

  await page.getByLabel("Meteorite name").fill("Northwest Africa 15000");
  await page.getByLabel("Classification").fill("Lunar feldspathic breccia");
  await page.locator('input[name="certificateId"]').fill("MUS-2026-0042");
  await page.getByLabel("Collection or business").fill("Natural History Research Collection");
  await page.getByLabel("Weight (grams)").fill("18.25");

  const styles = [
    ["Regal Archive", "regal-archive"],
    ["Museum Ledger", "museum-ledger"],
    ["Celestial Formal", "celestial-formal"],
  ] as const;
  const artifactDirectory = process.env.COA_ARTIFACT_DIR;
  if (artifactDirectory) await mkdir(artifactDirectory, { recursive: true });

  for (const width of [2048, 1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    for (const [name, id] of styles) {
      await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
      await expect(preview).toHaveAttribute("data-certificate-style", id);
      const geometry = await preview.evaluate((element) => {
        const box = (selector: string) => element.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON();
        const fontSize = (selector: string) => Number.parseFloat(getComputedStyle(element.querySelector(selector)!).fontSize);
        const heading = element.querySelector<HTMLElement>(".certificate-preview__header > strong")!;
        const meteoriteName = element.querySelector<HTMLElement>(".certificate-preview__title h3")!;
        const recordType = element.querySelector<HTMLElement>(".certificate-preview__record-type")!;
        const collection = element.querySelector<HTMLElement>(".certificate-preview__collection")!;
        const idLabel = element.querySelector<HTMLElement>(".certificate-preview__id span")!;
        const idValue = element.querySelector<HTMLElement>(".certificate-preview__id > strong")!;
        const factLabels = Array.from(element.querySelectorAll<HTMLElement>(".certificate-preview__facts dt"));
        const factValues = Array.from(element.querySelectorAll<HTMLElement>(".certificate-preview__facts dd"));
        const frame = element.querySelector<HTMLElement>(".certificate-preview__frame")!;
        const canvas = element.querySelector<HTMLElement>(".certificate-preview__canvas")!;
        const certificateText = Array.from(frame.querySelectorAll<HTMLElement>(
          ".certificate-preview__collection, .certificate-preview__record-type, .certificate-preview__header > strong, .certificate-preview__id, .certificate-preview__id span, .certificate-preview__id > strong, .certificate-preview__title h3, .certificate-preview__title p, .certificate-preview__photo, .certificate-preview__facts dt, .certificate-preview__facts dd, .certificate-preview__weight span, .certificate-preview__weight strong, .certificate-preview__weight em, .certificate-preview__weight small, .certificate-preview__signoff span, .certificate-preview__signoff strong, .certificate-preview__signoff small, .certificate-preview__seal, .certificate-preview__seal small",
        ));
        return {
          headingText: heading.textContent,
          recordTypeText: recordType.textContent,
          idLabelText: idLabel.textContent,
          heading: heading.getBoundingClientRect().toJSON(),
          headingFits: heading.scrollWidth <= heading.clientWidth,
          meteoriteNameFits: meteoriteName.scrollWidth <= meteoriteName.clientWidth,
          collectionFits: collection.scrollWidth <= collection.clientWidth,
          idLabelFits: idLabel.scrollWidth <= idLabel.clientWidth,
          idValueFits: idValue.scrollWidth <= idValue.clientWidth,
          factLabelsFit: factLabels.every((node) => node.scrollWidth <= node.clientWidth),
          factValuesFit: factValues.every((node) => node.scrollWidth <= node.clientWidth),
          canonicalFrame: {
            width: Number.parseFloat(getComputedStyle(frame).width),
            height: Number.parseFloat(getComputedStyle(frame).height),
            scale: Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--certificate-preview-scale")),
            minimumFontSize: Math.min(...certificateText.map((node) => Number.parseFloat(getComputedStyle(node).fontSize))),
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
          photo: box(".certificate-preview__photo"),
          facts: box(".certificate-preview__facts"),
          summary: box(".certificate-preview__weight"),
          signoff: box(".certificate-preview__signoff"),
          seal: box(".certificate-preview__seal"),
          frame: box(".certificate-preview__frame"),
        };
      });
      expect(geometry.headingText).toBe("Certificate of Authenticity");
      expect(geometry.recordTypeText).toBe(id === "museum-ledger" ? "Signed specimen catalog" : "Archival specimen record");
      expect(geometry.idLabelText).toBe(id === "museum-ledger" ? "Catalog record / COA ID" : "Certificate ID");
      expect(geometry.headingFits, `${id} title clipping at ${width}px`).toBe(true);
      expect(geometry.meteoriteNameFits, `${id} representative meteorite name clipping at ${width}px`).toBe(true);
      expect(geometry.collectionFits, `${id} representative collection clipping at ${width}px`).toBe(true);
      expect(geometry.idLabelFits, `${id} representative ID label clipping at ${width}px`).toBe(true);
      expect(geometry.idValueFits, `${id} representative ID clipping at ${width}px`).toBe(true);
      expect(geometry.factLabelsFit, `${id} representative fact label clipping at ${width}px`).toBe(true);
      expect(geometry.factValuesFit, `${id} representative fact value clipping at ${width}px`).toBe(true);
      expect(geometry.canonicalFrame.width).toBe(1100);
      expect(geometry.canonicalFrame.height).toBe(850);
      expect(geometry.canonicalFrame.scale, `${id} scale at ${width}px`).toBeGreaterThan(0);
      expect(geometry.canonicalFrame.scale, `${id} scale at ${width}px`).toBeLessThanOrEqual(1);
      expect(geometry.canonicalFrame.minimumFontSize, `${id} canonical font floor at ${width}px`).toBeGreaterThanOrEqual(16);
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

  const longCertificateId = `COA-${"X".repeat(116)}`;
  const longOwner = "Long-form collection owner name ".repeat(8).trim();
  await page.locator('input[name="certificateId"]').fill(longCertificateId);
  await page.getByLabel("Recorded owner").fill(longOwner);
  await expect(preview.locator(".certificate-preview__id > strong")).toHaveText(longCertificateId);
  for (const [name, id] of styles) {
    await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
    await expect(preview).toHaveAttribute("data-certificate-style", id);
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
  await page.getByLabel("Recorded owner").fill("");

  await stylePicker.getByRole("radio", { name: /Museum Ledger/ }).check();
  const museumSignature = await preview.evaluate((element) => {
    const id = getComputedStyle(element.querySelector(".certificate-preview__id")!);
    const facts = getComputedStyle(element.querySelector(".certificate-preview__facts")!);
    const factLabel = getComputedStyle(element.querySelector(".certificate-preview__facts dt")!);
    const photoCaption = getComputedStyle(element.querySelector(".certificate-preview__photo")!, "::after");
    const weight = getComputedStyle(element.querySelector(".certificate-preview__weight")!);
    const seal = getComputedStyle(element.querySelector(".certificate-preview__seal")!);
    const watermark = getComputedStyle(element.querySelector(".certificate-preview__body")!, "::before");
    return {
      accessionBackground: id.backgroundColor,
      factsBorder: facts.borderTopWidth,
      factLabelBackground: factLabel.backgroundColor,
      photoCaption: photoCaption.content,
      measurementRail: weight.borderLeftWidth,
      sealRadius: seal.borderRadius,
      sealBorder: seal.borderTopWidth,
      watermark: watermark.backgroundImage,
    };
  });
  expect(museumSignature.accessionBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(Number.parseFloat(museumSignature.factsBorder)).toBeGreaterThanOrEqual(2);
  expect(museumSignature.factLabelBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(museumSignature.photoCaption).toContain("Documentation plate / 01");
  expect(Number.parseFloat(museumSignature.measurementRail)).toBeGreaterThanOrEqual(6);
  expect(museumSignature.sealRadius).toBe("50%");
  expect(Number.parseFloat(museumSignature.sealBorder)).toBeGreaterThanOrEqual(2);
  expect(museumSignature.watermark).toContain("linear-gradient");
  expect(museumSignature.watermark).not.toContain("radial-gradient");

  await stylePicker.getByRole("radio", { name: /Regal Archive/ }).check();
  const regalSignature = await preview.evaluate((element) => {
    const frame = getComputedStyle(element.querySelector(".certificate-preview__frame")!);
    const title = getComputedStyle(element.querySelector(".certificate-preview__title")!);
    const photo = getComputedStyle(element.querySelector(".certificate-preview__photo")!);
    const facts = getComputedStyle(element.querySelector(".certificate-preview__facts")!);
    const weight = getComputedStyle(element.querySelector(".certificate-preview__weight")!);
    const seal = getComputedStyle(element.querySelector(".certificate-preview__seal")!);
    return {
      frameShadow: frame.boxShadow,
      titleAlignment: title.textAlign,
      photoBorder: photo.borderTopStyle,
      factsBorder: facts.borderTopStyle,
      weightBackground: weight.backgroundColor,
      weightColor: weight.color,
      sealShadow: seal.boxShadow,
    };
  });
  expect(regalSignature.frameShadow).not.toBe("none");
  expect(regalSignature.titleAlignment).toBe("center");
  expect(regalSignature.photoBorder).toBe("double");
  expect(regalSignature.factsBorder).toBe("double");
  expect(regalSignature.weightBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(regalSignature.weightColor).not.toBe("rgb(255, 255, 255)");
  expect(regalSignature.sealShadow).not.toBe("none");

  if (artifactDirectory) {
    await page.setViewportSize({ width: 1280, height: 1000 });
    for (const [name, id] of styles) {
      await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
      await preview.screenshot({ path: join(artifactDirectory, `filled-${id}-1280.png`) });
    }
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await stylePicker.getByRole("radio", { name: /Museum Ledger/ }).check();
      await preview.screenshot({ path: join(artifactDirectory, `filled-museum-ledger-${width}.png`) });
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
  const stylePicker = page.getByRole("group", { name: "Certificate layout style" });
  for (const [name, id] of [
    ["Regal Archive", "regal-archive"],
    ["Museum Ledger", "museum-ledger"],
    ["Celestial Formal", "celestial-formal"],
  ] as const) {
    await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
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
  const stylePicker = page.getByRole("group", { name: "Certificate layout style" });
  const themePicker = page.getByRole("group", { name: "Certificate color scheme" });
  await expect(stylePicker.getByRole("radio")).toHaveCount(3);
  await expect(themePicker.getByRole("radio")).toHaveCount(9);
  await expect(stylePicker.getByRole("radio", { name: /Regal Archive/ })).toBeChecked();
  await expect(themePicker.getByRole("radio", { name: /Observatory Navy/ })).toBeChecked();

  const proofChain = page.locator(".hero__ledger");
  const proofChainBox = await proofChain.boundingBox();
  expect(proofChainBox?.width).toBeGreaterThan(450);
  expect(Number.parseFloat(await proofChain.locator("li strong").first().evaluate((element) => getComputedStyle(element).fontSize)))
    .toBeGreaterThanOrEqual(22);

  const certificatePreview = page.locator(".certificate-preview");
  const fallbackMark = certificatePreview.locator(".certificate-preview__collection > .orbit-mark");
  await expect(fallbackMark).toBeVisible();
  await expect(certificatePreview.locator(".certificate-preview__logo")).toHaveCount(0);

  const logoInput = page.getByLabel("Logo");
  await logoInput.setInputFiles({ name: "tiny-collection-logo.png", mimeType: "image/png", buffer: onePixelPng });
  const liveLogo = certificatePreview.locator(".certificate-preview__collection > img.certificate-preview__logo");
  await expect(liveLogo).toBeVisible();
  await expect(liveLogo).toHaveAttribute("src", /^blob:/);
  await expect(liveLogo).toHaveAttribute("alt", "Collection logo");
  const firstLogoSource = await liveLogo.getAttribute("src");
  const logoGeometry = await liveLogo.evaluate((element) => {
    const image = element as HTMLImageElement;
    const style = getComputedStyle(image);
    const box = image.getBoundingClientRect();
    const collectionBox = image.parentElement!.getBoundingClientRect();
    return {
      objectFit: style.objectFit,
      objectPosition: style.objectPosition,
      width: box.width,
      height: box.height,
      contained: box.left >= collectionBox.left && box.right <= collectionBox.right
        && box.top >= collectionBox.top && box.bottom <= collectionBox.bottom,
    };
  });
  expect(logoGeometry.objectFit).toBe("contain");
  expect(logoGeometry.objectPosition).toBe("50% 50%");
  expect(logoGeometry.width).toBeGreaterThan(0);
  expect(logoGeometry.height).toBeGreaterThan(0);
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
  for (const [name, id] of [
    ["Regal Archive", "regal-archive"],
    ["Museum Ledger", "museum-ledger"],
    ["Celestial Formal", "celestial-formal"],
  ] as const) {
    await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
    await expect(certificatePreview).toHaveAttribute("data-certificate-style", id);
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
  expect(styleSignatures.size).toBe(3);

  const themeSignatures = new Set<string>();
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
  expect(themeSignatures.size).toBe(9);
  await expect(certificatePreview).toHaveAttribute("data-certificate-style", "celestial-formal");

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

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".site-header")).toBeVisible();
  await expect(liveLogo).toBeVisible();
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);

  await logoInput.setInputFiles([]);
  await expect(liveLogo).toHaveCount(0);
  await expect(fallbackMark).toBeVisible();
  expect(errors).toEqual([]);
});

test("keeps certificate facts, signoff, and non-active status treatments disjoint", async ({ page }) => {
  await page.goto("/#builder");
  const statusSelect = page.locator('select[name="certificateStatus"]');
  await statusSelect.selectOption("revoked", { force: true });

  const certificatePreview = page.locator(".certificate-preview");
  const status = certificatePreview.locator(".certificate-preview__status");
  const stylePicker = page.getByRole("group", { name: "Certificate layout style" });
  const styles = [
    ["Regal Archive", "regal-archive"],
    ["Museum Ledger", "museum-ledger"],
    ["Celestial Formal", "celestial-formal"],
  ] as const;

  for (const width of [1280, 760, 390, 320]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });

    for (const [name, id] of styles) {
      await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
      await expect(certificatePreview).toHaveAttribute("data-certificate-style", id);
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
      expect(Number.parseFloat(await status.evaluate((element) => getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(6);

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

test("generates, downloads, verifies, and rejects tampering", async ({ page }, testInfo) => {
  await page.goto("/#builder");

  await page.locator('input[name="issuerName"]').fill("Test Issuer");
  await page.locator('input[name="collectionName"]').fill("Test Meteorite Collection");
  await page.locator('input[name="certificateId"]').fill("TEST-COA-0001");
  await page.locator('input[name="issueDate"]').fill("2026-07-29");
  await page.locator('input[name="certificateVersion"]').fill("1.0");
  await page.locator('input[name="meteoriteName"]').fill("Test Meteorite");
  await page.locator('input[name="classification"]').fill("L5 chondrite");
  await page.locator('input[name="weightGrams"]').fill("12.3");
  await page.locator('input[name="weightPrecision"]').fill("0.1");
  await page.locator('select[name="specimenForm"]').selectOption({ label: "Fragment" });
  await page.locator('input[name="numberOfPieces"]').fill("1");
  await page.locator('input[name="recordedOwner"]').fill("Test Owner");
  await page.locator("details", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.locator('input[name="fallStatus"]').fill("Find");
  await page.locator('input[name="fallDate"]').fill("2024-01-15");
  await page.locator('input[name="country"]').fill("Canada");
  await page.locator('input[name="locality"]').fill("Example Township");
  await page.locator('input[name="latitude"]').fill("45.4215 N");
  await page.locator('input[name="longitude"]').fill("75.6972 W");
  await page.locator('textarea[name="provenance"]').fill("Documented test custody from recovery through issuance.");

  await page.getByRole("radio", { name: /Royal Amethyst/ }).check();
  await page.getByRole("radio", { name: /Museum Ledger/ }).check();
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
    buffer: onePixelPng,
  });
  await page.getByLabel(/I attest this is an exact/).check();
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

  const packageDownload = page.waitForEvent("download", { timeout: 90_000 });
  await page.getByRole("button", { name: "Issue signed COA package" }).click();
  const download = await packageDownload;
  const packagePath = await download.path();
  expect(packagePath).toBeTruthy();
  expect(download.suggestedFilename()).toContain("TEST-COA-0001");
  await expect(page.getByText("Release created")).toBeVisible();

  const packageBuffer = await readFile(packagePath!);
  const archive = await JSZip.loadAsync(packageBuffer);
  const manifestName = Object.keys(archive.files).find((name) => name.endsWith("/manifest.json"));
  expect(manifestName).toBeTruthy();
  const root = manifestName!.slice(0, -"manifest.json".length);
  const manifest = JSON.parse(await archive.file(manifestName!)!.async("text")) as {
    certificate: { visualStyle?: string; visualTheme?: string };
    files: Array<{ path: string }>;
  };
  expect(manifest.certificate.visualStyle).toBe("museum-ledger");
  expect(manifest.certificate.visualTheme).toBe("royal-amethyst");
  const signedPaths = manifest.files.map((entry) => entry.path);
  expect(signedPaths).toEqual(expect.arrayContaining([
    "README-FIRST.txt",
    "coa-manifest-v1.schema.json",
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
      join(process.env.COA_ARTIFACT_DIR, "revoked-museum-ledger-certificate.png"),
      await archive.file(`${root}certificate.png`)!.async("nodebuffer"),
    );
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-museum-ledger-certificate.pdf"),
      await archive.file(`${root}certificate.pdf`)!.async("nodebuffer"),
    );

    await page.getByRole("radio", { name: /Regal Archive/ }).check();
    await expect(page.locator(".certificate-preview")).toHaveAttribute("data-certificate-style", "regal-archive");
    const regalPackageDownload = page.waitForEvent("download", { timeout: 90_000 });
    await page.getByRole("button", { name: "Issue signed COA package" }).click();
    const regalDownload = await regalPackageDownload;
    const regalPackagePath = await regalDownload.path();
    expect(regalPackagePath).toBeTruthy();
    const regalArchive = await JSZip.loadAsync(await readFile(regalPackagePath!));
    const regalManifestName = Object.keys(regalArchive.files).find((name) => name.endsWith("/manifest.json"));
    expect(regalManifestName).toBeTruthy();
    const regalRoot = regalManifestName!.slice(0, -"manifest.json".length);
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-regal-archive-certificate.png"),
      await regalArchive.file(`${regalRoot}certificate.png`)!.async("nodebuffer"),
    );
    await writeFile(
      join(process.env.COA_ARTIFACT_DIR, "revoked-regal-archive-certificate.pdf"),
      await regalArchive.file(`${regalRoot}certificate.pdf`)!.async("nodebuffer"),
    );
  }
  const certificateRecord = JSON.parse(await archive.file(`${root}certificate-record.json`)!.async("text")) as {
    certificate: { visualStyle?: string; visualTheme?: string };
  };
  expect(certificateRecord.certificate.visualStyle).toBe("museum-ledger");
  expect(certificateRecord.certificate.visualTheme).toBe("royal-amethyst");
  const certificateText = await archive.file(`${root}certificate.txt`)!.async("text");
  expect(certificateText).toContain("Certificate layout style: Museum Ledger");
  expect(certificateText).toContain("Certificate color scheme: Royal Amethyst");
  const packagedSchema = JSON.parse(await archive.file(`${root}coa-manifest-v1.schema.json`)!.async("text")) as {
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
  }

  await page.locator("#verify").scrollIntoViewIfNeeded();
  await page.locator(".verifier input[type=file]").setInputFiles(packagePath!);
  await expect(page.locator(".verifier__report-head").getByText("PASS")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("All required cryptographic checks passed.")).toBeVisible();

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
