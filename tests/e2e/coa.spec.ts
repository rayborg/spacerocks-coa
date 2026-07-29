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

test("loads the workbench on desktop and mobile without horizontal overflow", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /certificate is only as enduring/i })).toBeVisible();
  await expect(page.getByLabel("Weight (grams)")).toHaveValue("44.7");
  await expect(page.getByLabel("Specimen form")).toHaveValue("Half stone / end cut");
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
  await expect(liveLogo).toHaveAttribute("alt", "The Spacerocks Collection logo");
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
  await expect(certificatePreview.getByText("Specimen details")).toBeVisible();

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

  await page.getByRole("radio", { name: /Royal Amethyst/ }).check();
  await page.getByRole("radio", { name: /Museum Ledger/ }).check();

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
  expect(download.suggestedFilename()).toContain("AZ-2019-0447-HE");
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
