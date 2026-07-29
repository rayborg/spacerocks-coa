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
  const styleSignatures = new Set<string>();
  for (const [name, id] of [
    ["Regal Archive", "regal-archive"],
    ["Museum Ledger", "museum-ledger"],
    ["Celestial Formal", "celestial-formal"],
  ] as const) {
    await stylePicker.getByRole("radio", { name: new RegExp(name) }).check();
    await expect(certificatePreview).toHaveAttribute("data-certificate-style", id);
    styleSignatures.add(await certificatePreview.evaluate((element) => {
      const previewStyle = getComputedStyle(element);
      const headerStyle = getComputedStyle(element.querySelector(".certificate-preview__header")!);
      return [previewStyle.borderTopWidth, headerStyle.backgroundColor, headerStyle.backgroundImage].join("|");
    }));
  }
  expect(styleSignatures.size).toBe(3);
  await expect(themePicker.getByRole("radio", { name: /Observatory Navy/ })).toBeChecked();

  await themePicker.getByRole("radio", { name: /Museum Burgundy/ }).check();
  await expect(certificatePreview).toHaveAttribute("data-certificate-theme", "museum-burgundy");
  await expect(certificatePreview).toHaveAttribute("data-certificate-style", "celestial-formal");
  expect(await certificatePreview.evaluate((element) => getComputedStyle(element).getPropertyValue("--certificate-dark").trim()))
    .toBe("#3b0d18");

  const photoBox = await certificatePreview.locator(".certificate-preview__photo").boundingBox();
  const detailsBox = await certificatePreview.locator(".certificate-preview__weight").boundingBox();
  const sealBox = await certificatePreview.locator(".certificate-preview__seal").boundingBox();
  expect(photoBox && detailsBox && sealBox).toBeTruthy();
  expect(detailsBox!.y).toBeGreaterThanOrEqual(photoBox!.y + photoBox!.height);
  expect(sealBox!.y).toBeGreaterThanOrEqual(detailsBox!.y + detailsBox!.height);
  expect(sealBox!.width).toBeLessThan(detailsBox!.width / 2);
  await expect(certificatePreview.getByText("Specimen details")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".site-header")).toBeVisible();
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  expect(errors).toEqual([]);
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
