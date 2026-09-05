import { expect, test } from "@playwright/test";

const record = {
  code: 87447,
  official_url: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=87447",
  canonical_name: "Northwest Africa 18652",
  record_status: "Relict",
  official_name: true,
  recommended_classification: "Relict iron",
  fall_or_find: "Find",
  year_found: 2018,
  country: "Western Sahara",
  latitude: null,
  longitude: null,
};

test("autofills only authoritative MetBull fields and requires fresh attestation", async ({ page }) => {
  const apiUrl = process.env.VITE_TIMESTAMP_API_URL;
  test.skip(!apiUrl, "timestamp API configuration is required");
  let requests = 0;
  await page.route(`${apiUrl}/v1/meteorites/metbull?code=87447`, async (route) => {
    requests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "cache-control": "no-store" },
      body: JSON.stringify(record),
    });
  });

  await page.goto("/#builder");
  await page.getByRole("radio", { name: /Official/ }).click();
  await page.getByLabel("Meteorite type").fill("Issuer-reviewed type");
  await page.getByLabel("Meteorite class").fill("Old classification");
  await page.getByLabel("Meteorite subclass").fill("Issuer-reviewed subclass");
  await page.getByLabel("Weight (grams)").fill("2.4");
  await page.getByLabel("Specimen form").selectOption("Fragment");
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.getByLabel("Region (optional)").fill("Issuer region");
  await page.getByLabel("Locality / city (optional)").fill("Issuer locality");
  await page.getByLabel("Finder name (optional)").fill("Documented finder");
  await page.getByLabel("Previous owner (optional)").fill("Documented prior owner");
  await page.getByLabel("Meteoritical Bulletin code").fill("87447");
  await page.getByLabel("Official name verification").check();

  expect(requests).toBe(0);
  await page.getByRole("button", { name: "Fill from Meteoritical Bulletin" }).click();
  await expect(page.getByRole("status")).toContainText("Loaded Northwest Africa 18652");
  expect(requests).toBe(1);
  await expect(page.getByLabel("Official canonical meteorite name")).toHaveValue("Northwest Africa 18652");
  await expect(page.getByLabel("Meteorite class")).toHaveValue("Relict iron");
  await expect(page.getByLabel("Fall or find status")).toHaveValue("Find");
  await expect(page.getByLabel("Country")).toHaveValue("Western Sahara");
  await expect(page.getByLabel("Official Meteoritical Bulletin URL")).toHaveValue(record.official_url);
  await expect(page.getByLabel("Official name verification")).not.toBeChecked();

  await expect(page.getByLabel("Meteorite type")).toHaveValue("Issuer-reviewed type");
  await expect(page.getByLabel("Meteorite subclass")).toHaveValue("Issuer-reviewed subclass");
  await expect(page.getByLabel("Weight (grams)")).toHaveValue("2.4");
  await expect(page.getByLabel("Specimen form")).toHaveValue("Fragment");
  await expect(page.getByLabel("Region (optional)")).toHaveValue("Issuer region");
  await expect(page.getByLabel("Locality / city (optional)")).toHaveValue("Issuer locality");
  await expect(page.getByLabel("Finder name (optional)")).toHaveValue("Documented finder");
  await expect(page.getByLabel("Previous owner (optional)")).toHaveValue("Documented prior owner");
});

test("ignores a stale lookup after the code changes", async ({ page }) => {
  const apiUrl = process.env.VITE_TIMESTAMP_API_URL;
  test.skip(!apiUrl, "timestamp API configuration is required");
  let release: (() => void) | undefined;
  await page.route(`${apiUrl}/v1/meteorites/metbull?code=87447`, async (route) => {
    await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ status: 200, contentType: "application/json", headers: { "cache-control": "no-store" }, body: JSON.stringify(record) });
  });
  await page.goto("/#builder");
  await page.getByRole("radio", { name: /Official/ }).click();
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  const code = page.getByLabel("Meteoritical Bulletin code");
  await code.fill("87447");
  await page.getByRole("button", { name: "Fill from Meteoritical Bulletin" }).click();
  await code.fill("12345");
  release?.();
  await expect(page.getByLabel("Official canonical meteorite name")).not.toHaveValue("Northwest Africa 18652");
  await expect(code).toHaveValue("12345");
});
