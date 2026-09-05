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

const ordinaryRecords = [
  { code: 69696, canonical_name: "Aguas Zarcas", recommended_classification: "CM2", fall_or_find: "Fall", year_found: 2019, country: "Costa Rica", latitude: "10.391400", longitude: "-84.341270" },
  { code: 2278, canonical_name: "Allende", recommended_classification: "CV3", fall_or_find: "Fall", year_found: 1969, country: "Mexico", latitude: "26.966670", longitude: "-105.316670" },
  { code: 57165, canonical_name: "Chelyabinsk", recommended_classification: "LL5", fall_or_find: "Fall", year_found: 2013, country: "Russia", latitude: "54.816670", longitude: "61.116670" },
  { code: 16875, canonical_name: "Murchison", recommended_classification: "CM2", fall_or_find: "Fall", year_found: 1969, country: "Australia", latitude: "-36.616670", longitude: "145.200000" },
  { code: 74388, canonical_name: "Winchcombe", recommended_classification: "CM2", fall_or_find: "Fall", year_found: 2021, country: "United Kingdom", latitude: "51.945000", longitude: "-2.032000" },
].map((entry) => ({
  ...entry,
  official_url: `https://www.lpi.usra.edu/meteor/metbull.cfm?code=${entry.code}`,
  record_status: "Official",
  official_name: true,
}));

const recordWithoutCountry = {
  code: 378,
  official_url: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=378",
  canonical_name: "Adelie Land",
  record_status: "Official",
  official_name: true,
  recommended_classification: "L5",
  fall_or_find: "Find",
  year_found: 1912,
  country: null,
  latitude: "-67.183330",
  longitude: "142.383330",
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
  await expect(page.getByLabel("Meteoritical Bulletin code")).toBeVisible();
  await expect(page.getByRole("button", { name: "Fill from Meteoritical Bulletin" })).toBeVisible();
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.getByLabel("Region (optional)").fill("Issuer region");
  await page.getByLabel("Locality / city (optional)").fill("Issuer locality");
  await page.getByLabel("Finder name (optional)").fill("Documented finder");
  await page.getByLabel("Previous owner (optional)").fill("Documented prior owner");
  await page.getByLabel("Official Meteoritical Bulletin URL").fill(record.official_url);
  await expect(page.getByLabel("Meteoritical Bulletin code (from URL)")).toHaveValue("87447");
  await page.getByLabel("Official name verification").check();

  expect(requests).toBe(0);
  await page.getByRole("button", { name: "Fill from Meteoritical Bulletin" }).click();
  await expect(page.getByRole("status")).toContainText("Loaded Northwest Africa 18652");
  expect(requests).toBe(1);
  await expect(page.getByLabel("Official canonical meteorite name")).toHaveValue("Northwest Africa 18652");
  await expect(page.getByLabel("Meteorite class")).toHaveValue("Relict iron");
  await expect(page.getByLabel("Fall or find status")).toHaveValue("Find");
  await expect(page.getByLabel("Country")).toHaveValue("Morocco");
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

test("autofills unrelated meteorites without changing their countries or coordinates", async ({ page }) => {
  const apiUrl = process.env.VITE_TIMESTAMP_API_URL;
  test.skip(!apiUrl, "timestamp API configuration is required");
  for (const entry of ordinaryRecords) {
    await page.route(`${apiUrl}/v1/meteorites/metbull?code=${entry.code}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "cache-control": "no-store" },
        body: JSON.stringify(entry),
      });
    });
  }
  await page.route(`${apiUrl}/v1/meteorites/metbull?code=${recordWithoutCountry.code}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "cache-control": "no-store" },
      body: JSON.stringify(recordWithoutCountry),
    });
  });

  await page.goto("/#builder");
  await page.getByRole("radio", { name: /Official/ }).click();
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  const url = page.getByLabel("Official Meteoritical Bulletin URL");
  const lookup = page.getByRole("button", { name: "Fill from Meteoritical Bulletin" });
  for (const entry of ordinaryRecords) {
    await url.fill(entry.official_url);
    await expect(page.getByLabel("Meteoritical Bulletin code (from URL)")).toHaveValue(String(entry.code));
    await lookup.click();
    await expect(page.getByRole("status")).toContainText(`Loaded ${entry.canonical_name}`);
    await expect(page.getByLabel("Official canonical meteorite name")).toHaveValue(entry.canonical_name);
    await expect(page.getByLabel("Meteorite class")).toHaveValue(entry.recommended_classification);
    await expect(page.getByLabel("Fall or find status")).toHaveValue(entry.fall_or_find);
    await expect(page.getByLabel("Country")).toHaveValue(entry.country);
    await expect(page.getByLabel("Latitude (optional)")).toHaveValue(entry.latitude);
    await expect(page.getByLabel("Longitude (optional)")).toHaveValue(entry.longitude);
  }
  await page.getByLabel("Country").fill("Antarctica");
  await url.fill(recordWithoutCountry.official_url);
  await lookup.click();
  await expect(page.getByRole("status")).toContainText(`Loaded ${recordWithoutCountry.canonical_name}`);
  await expect(page.getByLabel("Official canonical meteorite name")).toHaveValue(recordWithoutCountry.canonical_name);
  await expect(page.getByLabel("Country")).toHaveValue("Antarctica");
  await expect(page.getByLabel("Latitude (optional)")).toHaveValue(recordWithoutCountry.latitude);
  await expect(page.getByLabel("Longitude (optional)")).toHaveValue(recordWithoutCountry.longitude);
});

test("revalidates missing type and subclass immediately after the rare-record attestation", async ({ page }) => {
  const apiUrl = process.env.VITE_TIMESTAMP_API_URL;
  test.skip(!apiUrl, "timestamp API configuration is required");
  await page.route(`${apiUrl}/v1/meteorites/metbull?code=87447`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", headers: { "cache-control": "no-store" }, body: JSON.stringify(record) });
  });
  await page.goto("/#builder");
  await page.getByRole("radio", { name: /Official/ }).click();
  await page.getByLabel("Official Meteoritical Bulletin URL").fill(record.official_url);
  await page.getByRole("button", { name: "Fill from Meteoritical Bulletin" }).click();
  await expect(page.getByRole("status")).toContainText("Loaded Northwest Africa 18652");
  await expect(page.getByLabel("Meteorite type")).toHaveValue("");
  await expect(page.getByLabel("Meteorite subclass")).toHaveValue("");
  await page.getByLabel("Missing MetBull classification details").check();
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.getByLabel("Official name verification").check();
  await expect(page.getByText("Enter the official value or attest below that the linked MetBull entry does not provide it.")).toHaveCount(0);
  await expect(page.getByText("Use this exception only when the official entry omits the type or subclass.")).toHaveCount(0);
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
  const url = page.getByLabel("Official Meteoritical Bulletin URL");
  const code = page.getByLabel("Meteoritical Bulletin code (from URL)");
  await url.fill(record.official_url);
  await page.getByRole("button", { name: "Fill from Meteoritical Bulletin" }).click();
  await url.fill("https://www.lpi.usra.edu/meteor/metbull.cfm?code=12345");
  release?.();
  await expect(page.getByLabel("Official canonical meteorite name")).not.toHaveValue("Northwest Africa 18652");
  await expect(code).toHaveValue("12345");
});
