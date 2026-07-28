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

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".site-header")).toBeVisible();
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  expect(errors).toEqual([]);
});

test("generates, downloads, verifies, and rejects tampering", async ({ page }, testInfo) => {
  await page.goto("/#builder");

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
    files: Array<{ path: string }>;
  };
  const signedPaths = manifest.files.map((entry) => entry.path);
  expect(signedPaths).toEqual(expect.arrayContaining([
    "README-FIRST.txt",
    "coa-manifest-v1.schema.json",
    "public-key.pem",
    "verification-instructions.txt",
    "verify.py",
  ]));
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
