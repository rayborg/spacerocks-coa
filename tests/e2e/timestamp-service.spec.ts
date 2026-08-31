import { expect, test, type Page } from "@playwright/test";

const apiConfigured = Boolean(process.env.VITE_TIMESTAMP_API_URL);
const serviceMode = process.env.VITE_TIMESTAMP_SERVICE_MODE ?? "sandbox";

function validProductionUrl(value: string | undefined): boolean {
  if (!value || value.trim() !== value || value.length > 2048) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash;
  } catch {
    return false;
  }
}

const productionConfigured = apiConfigured
  && serviceMode === "production"
  && /^(?!phase0)[A-Za-z0-9._-]{1,32}$/i.test(process.env.VITE_TIMESTAMP_POLICY_VERSION ?? "")
  && validProductionUrl(process.env.VITE_TIMESTAMP_TERMS_URL)
  && validProductionUrl(process.env.VITE_TIMESTAMP_PRIVACY_URL)
  && validProductionUrl(process.env.VITE_TIMESTAMP_REFUND_URL)
  && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(process.env.VITE_TIMESTAMP_SUPPORT_EMAIL ?? "")
  && (process.env.VITE_TIMESTAMP_SUPPORT_EMAIL?.length ?? 0) <= 254;
const sandboxConfigured = apiConfigured && serviceMode === "sandbox";
const timestampFeatureConfigured = sandboxConfigured || productionConfigured;
const digest = "cf0a31b01661599b8f73cd2dd2830f859e36a00c8ca22b259b33a7ec32c067cc";
const token = "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const replacementToken = "v1.BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
const orderReference = "ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB";
const checkoutSessionId = "cs_test_a1SafeSession9";
const checkoutUrl = `https://checkout.stripe.com/c/pay/${checkoutSessionId}#fidkdWxOYHwnPyd1blpxYHZxWjA0SDdUN1NGPEF8`;
const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

const pendingStatus = {
  order_reference: orderReference,
  certificate_reference: "TEST-COA-0001",
  manifest_sha256: digest,
  payment_state: "paid",
  fulfillment_state: "calendar_pending",
  created_at: "2026-07-30T12:00:00Z",
  updated_at: "2026-07-30T12:05:00Z",
  calendar_submitted_at: "2026-07-30T12:05:00Z",
  proof_available: true,
  message_code: "bitcoin_confirmation_pending",
};

const privateHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, cache-control, content-type, idempotency-key",
  "access-control-expose-headers": "cache-control, content-disposition, content-length, content-type",
  "cache-control": "no-store",
  "content-type": "application/json",
};

async function fulfillOptions(page: Page): Promise<void> {
  await page.route("**/v1/**", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: { ...privateHeaders, "access-control-allow-methods": "GET, POST, OPTIONS" }, body: "" });
      return;
    }
    await route.abort();
  });
}

async function fulfillCheckoutPrice(page: Page, amountMinor = 999): Promise<void> {
  await page.route("**/v1/checkout/price", async (route) => {
    await route.fulfill({
      status: 200,
      headers: privateHeaders,
      body: JSON.stringify({ amount_minor: amountMinor, currency: "usd" }),
    });
  });
}

async function issueLocalRelease(page: Page, issueButtonName = "Issue cryptographically signed COA package"): Promise<void> {
  await page.locator('input[name="issuerName"]').fill("Test Issuer");
  await page.locator('input[name="collectionName"]').fill("Test Meteorite Collection");
  await page.locator('input[name="certificateId"]').fill("TEST-COA-0001");
  await page.locator('input[name="issueDate"]').fill("2026-07-30");
  await page.locator('input[name="certificateVersion"]').fill("1.0");
  await page.locator('input[name="meteoriteName"]').fill("Test Meteorite");
  await page.locator('input[name="weightGrams"]').fill("12.3");
  await page.locator('input[name="weightPrecision"]').fill("0.1");
  await page.locator('select[name="specimenForm"]').selectOption({ label: "Fragment" });
  await page.locator('input[name="numberOfPieces"]').fill("1");
  await page.locator('input[name="recordedOwner"]').fill("Test Owner");
  await page.locator("details.workbench-section", { hasText: "Fall, find, and provenance" }).locator("summary").click();
  await page.locator('input[name="fallStatus"]').fill("Find");
  await page.locator('input[name="fallDate"]').fill("2024-01-15");
  await page.locator('input[name="country"]').fill("Canada");
  await page.locator('input[name="locality"]').fill("Example Township");
  await page.locator('input[name="latitude"]').fill("45.4215 N");
  await page.locator('input[name="longitude"]').fill("75.6972 W");
  await page.locator('textarea[name="provenance"]').fill("Documented test custody from recovery through issuance.");

  const keyForm = page.locator(".key-option").first();
  await keyForm.getByLabel("Passphrase", { exact: true }).fill("correct horse battery staple");
  await keyForm.getByLabel("Confirm passphrase").fill("correct horse battery staple");
  await keyForm.getByRole("button", { name: "Generate Ed25519 key" }).click();
  await expect(page.getByText("Key loaded", { exact: true })).toBeVisible({ timeout: 30_000 });
  const backup = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download encrypted key backup" }).click();
  await backup;

  await page.locator(".photo-drop input[type=file]").setInputFiles({
    name: "exact-specimen.png",
    mimeType: "image/png",
    buffer: onePixelPng,
  });
  await page.getByLabel(/I attest this is an exact/).check();
  const packageDownload = page.waitForEvent("download", { timeout: 90_000 });
  await page.getByRole("button", { name: issueButtonName }).click();
  await packageDownload;
  await expect(page.getByText("Release created")).toBeVisible();
}

test("omits the paid feature and makes zero timestamp requests without API configuration", async ({ page }) => {
  test.skip(timestampFeatureConfigured, "This assertion requires the timestamp frontend configuration to be absent or rejected at server start.");
  let timestampRequests = 0;
  await page.route("**/v1/**", async (route) => {
    timestampRequests += 1;
    await route.abort();
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Cryptographically Signed COA + blockchain timestamp" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Recover a timestamp order" })).toHaveCount(0);
  await expect(page.getByText("Sandbox / test only")).toHaveCount(0);
  await page.waitForTimeout(500);
  expect(timestampRequests).toBe(0);
});

test("advertises free and blockchain COA options before the builder", async ({ page }) => {
  test.skip(!timestampFeatureConfigured, "This assertion requires a configured timestamp frontend.");
  await fulfillCheckoutPrice(page);
  await page.goto("/");
  await expect(page.locator(".local-badge")).toContainText("Local COA signing");
  await expect(page.locator(".local-badge")).not.toContainText("Local-only");
  await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "COA options" })).toBeVisible();
  await expect(page.locator(".ledger__foot")).toContainText("COA verification website dependency");
  await expect(page.getByRole("heading", { name: "Four open COA checks. No permanent verification middleman." })).toBeVisible();
  const options = page.locator("#coa-options");
  await expect(options.getByRole("heading", { name: "Two COA options. One signed foundation." })).toBeVisible();
  await expect(options.getByRole("heading", { name: "Cryptographically Signed COA", exact: true })).toBeVisible();
  await expect(options.getByRole("heading", { name: "Cryptographically Signed COA + blockchain timestamp" })).toBeVisible();
  await expect(options).toContainText("$0");
  await expect(options).toContainText("$9.99");
  await expect(options.getByText(/Option 1|Option 2 · Enhanced/)).toHaveCount(0);
  await expect(options).toContainText("Bitcoin is the specific blockchain used");
  const placement = await page.evaluate(() => ({
    options: document.querySelector("#coa-options")?.getBoundingClientRect().top,
    hero: document.querySelector(".hero")?.getBoundingClientRect().top,
  }));
  expect(placement.options).toBeLessThan(placement.hero!);
  await options.getByRole("link", { name: "Create COA + blockchain proof" }).click();
  await expect(page.locator(".selected-service")).toContainText("Cryptographically Signed COA + blockchain timestamp · $9.99");
  await expect(page.getByRole("button", { name: "Issue cryptographically signed COA for blockchain timestamp" })).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await issueLocalRelease(page, "Issue cryptographically signed COA for blockchain timestamp");
  const checkoutHeading = page.getByRole("heading", { name: "Managed blockchain timestamp" });
  await expect(checkoutHeading).toBeVisible();
  await expect(checkoutHeading).toBeFocused();
  await expect(page.locator(".security-note")).toContainText("Payment details go directly to Stripe");
  await expect(page.locator(".security-note")).toContainText("later status or proof requests use your private recovery code");
  await expect(page.locator(".timestamp-privacy-note").first()).toContainText("The checkout request sends only the listed checkout fields");
  await expect(page.locator(".timestamp-privacy-note").first()).toContainText("Later status and proof requests authenticate with the private recovery code");
});

test("keeps the paid path unavailable until a canonical price retry succeeds", async ({ page }) => {
  test.skip(!timestampFeatureConfigured, "This assertion requires a configured timestamp frontend.");
  let priceAvailable = false;
  await page.route("**/v1/checkout/price", async (route) => {
    await route.fulfill(priceAvailable
      ? { status: 200, headers: privateHeaders, body: JSON.stringify({ amount_minor: 999, currency: "usd" }) }
      : { status: 503, headers: privateHeaders, body: JSON.stringify({ detail: "checkout unavailable" }) });
  });
  await page.goto("/");

  const paidCard = page.locator(".service-option--blockchain");
  await expect(paidCard.locator(".service-option__price strong")).toHaveText("Unavailable");
  const paidAction = paidCard.locator(".button--gold");
  await expect(paidAction).toHaveAttribute("aria-disabled", "true");
  await expect(paidAction).not.toHaveAttribute("href", /.+/);
  await paidAction.evaluate((element) => (element as HTMLElement).click());
  await expect(page.locator(".selected-service")).toContainText("Free cryptographically signed COA · $0");
  priceAvailable = true;
  await paidCard.getByRole("button", { name: "Retry live price" }).click();
  await expect(paidCard.locator(".service-option__price strong")).toHaveText("$9.99");
  await expect(paidAction).toHaveAttribute("aria-disabled", "false");
  await expect(paidAction).toHaveAttribute("href", "#builder");
});

test("waits through a cold canonical price response", async ({ page }) => {
  test.skip(!timestampFeatureConfigured, "This assertion requires a configured timestamp frontend.");
  await page.route("**/v1/checkout/price", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 11_000));
    await route.fulfill({
      status: 200,
      headers: privateHeaders,
      body: JSON.stringify({ amount_minor: 999, currency: "usd" }),
    });
  });
  await page.goto("/");

  const price = page.locator(".service-option--blockchain .service-option__price strong");
  await expect(price).toHaveText("Loading price...");
  await expect(price).toHaveText("$9.99", { timeout: 15_000 });
});

test.describe("configured sandbox timestamp service", () => {
  test.skip(!sandboxConfigured, "Run with a timestamp API URL and sandbox mode at server start.");

  test("reveals only explicit recovery, keeps the token private, rotates, resumes, and downloads safely", async ({ page }) => {
    const consoleMessages: string[] = [];
    const observedRequests: Array<{ url: string; authorization?: string; referer?: string }> = [];
    let reportConfirmed = false;
    page.on("console", (message) => consoleMessages.push(message.text()));
    await page.route("**/v1/**", async (route) => {
      const request = route.request();
      if (request.method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: { ...privateHeaders, "access-control-allow-methods": "GET, POST, OPTIONS" }, body: "" });
        return;
      }
      if (request.url().endsWith("/v1/checkout/price")) {
        await route.fulfill({
          status: 200,
          headers: privateHeaders,
          body: JSON.stringify({ amount_minor: 999, currency: "usd" }),
        });
        return;
      }
      const headers = request.headers();
      observedRequests.push({ url: request.url(), authorization: headers.authorization, referer: headers.referer });
      if (request.url().endsWith("/v1/orders/status")) {
        await route.fulfill({
          status: 200,
          headers: privateHeaders,
          body: JSON.stringify(reportConfirmed ? {
            ...pendingStatus,
            fulfillment_state: "bitcoin_verified",
            updated_at: "2026-07-30T15:10:00Z",
            bitcoin_verified_at: "2026-07-30T15:10:00Z",
          } : pendingStatus),
        });
      } else if (request.url().endsWith("/v1/orders/rotate-token")) {
        await route.fulfill({ status: 200, headers: privateHeaders, body: JSON.stringify({ status_token: replacementToken }) });
      } else if (request.url().endsWith("/v1/orders/proof")) {
        await route.fulfill({
          status: 200,
          headers: {
            ...privateHeaders,
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="timestamp-proof.zip"',
          },
          body: Buffer.from([1, 2, 3, 4]),
        });
      } else {
        await route.abort();
      }
    });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toHaveCount(0);
    await page.getByRole("button", { name: "Recover a timestamp order" }).click();
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toBeVisible();
    await expect(page.getByText("Sandbox / test only")).toBeVisible();
    await expect(page.getByText(/does not prove authenticity, ownership, identity/)).toBeVisible();
    await page.getByLabel("Private recovery code").fill(token);
    await page.getByRole("button", { name: "Recover in this tab" }).click();
    await expect(page.getByText("Calendar proof pending")).toBeVisible();
    await expect(page.getByText(/not yet Bitcoin-confirmed/)).toBeVisible();
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toHaveCount(0);
    expect(new URL(page.url()).search).toBe("");
    expect(page.url()).not.toContain(token);

    await page.getByRole("button", { name: "Rotate recovery code" }).click();
    await expect(page.getByLabel("Recovery code")).toHaveValue(replacementToken);
    const stored = await page.evaluate((key) => sessionStorage.getItem(key), "spacerocks.timestamp.phase0");
    expect(JSON.parse(stored!).token).toBe(replacementToken);

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download proof bundle" }).click();
    const proof = await download;
    expect(proof.suggestedFilename()).toBe("TEST-COA-0001-bitcoin-timestamp.zip");
    expect(proof.suggestedFilename()).not.toContain("v1.");

    reportConfirmed = true;
    await page.reload();
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toBeVisible();
    await expect(page.getByLabel("Recovery code")).toHaveValue(replacementToken);
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toBeVisible();
    const verifiedStatus = page.locator(".timestamp-status");
    await expect(verifiedStatus).toContainText(/at least one Bitcoin confirmation/);
    await expect(verifiedStatus).toContainText(/subject to reorganization monitoring/);
    await expect(verifiedStatus).toContainText(/private recovery code.*later proof upgrades/i);
    await page.setViewportSize({ width: 320, height: 844 });
    const dimensions = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);

    for (const request of observedRequests) {
      expect(request.url).not.toContain(token);
      expect(request.url).not.toContain(replacementToken);
      expect(request.authorization).toMatch(/^Bearer v1\.[A-Za-z0-9_-]{43}$/);
      expect(request.referer).toBeUndefined();
    }
    expect(consoleMessages.join("\n")).not.toContain(token);
    expect(consoleMessages.join("\n")).not.toContain(replacementToken);

    await page.getByRole("button", { name: "Forget this order" }).click();
    await expect(page.locator("#timestamp-saved-token")).toHaveCount(0);
    await expect(page.getByLabel("Private recovery code")).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0"))).toBeNull();
  });

  test("creates checkout only after local release with the exact allowlisted body and strict Stripe link", async ({ page }) => {
    let checkoutRequest: { headers: Record<string, string>; body: Record<string, unknown>; url: string } | undefined;
    const checkoutRequests: Array<{ headers: Record<string, string>; body: Record<string, unknown>; url: string }> = [];
    let hostileAttempt = 0;
    await page.route("**/v1/**", async (route) => {
      const request = route.request();
      if (request.method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: { ...privateHeaders, "access-control-allow-methods": "GET, POST, OPTIONS" }, body: "" });
        return;
      }
      if (request.url().endsWith("/v1/checkout")) {
        hostileAttempt += 1;
        checkoutRequest = { headers: request.headers(), body: request.postDataJSON(), url: request.url() };
        checkoutRequests.push(checkoutRequest);
        if (hostileAttempt === 1) {
          await route.abort("failed");
          return;
        }
        if (hostileAttempt === 2) {
          await route.fulfill({ status: 503, headers: privateHeaders, body: JSON.stringify({ detail: "response uncertain" }) });
          return;
        }
        if (hostileAttempt === 5) {
          await route.fulfill({ status: 422, headers: privateHeaders, body: JSON.stringify({ detail: "deterministic rejection" }) });
          return;
        }
        await route.fulfill({
          status: 201,
          headers: privateHeaders,
          body: JSON.stringify({
            order_reference: orderReference,
            status_token: token,
            checkout_url: hostileAttempt === 3
              ? "https://checkout.stripe.com.evil.test/c/pay/lookalike"
              : checkoutUrl,
            payment_state: "checkout_open",
            fulfillment_state: "awaiting_payment",
            ...(hostileAttempt === 4 ? { unexpected_customer_data: "must be rejected" } : {}),
          }),
        });
      } else if (request.url().endsWith("/v1/orders/status")) {
        await route.fulfill({ status: 200, headers: privateHeaders, body: JSON.stringify(pendingStatus) });
      } else {
        await route.abort();
      }
    });

    await page.goto("/#builder");
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toHaveCount(0);
    await issueLocalRelease(page);
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Create server-priced test checkout" })).toBeDisabled();
    const deliveryEmail = page.getByLabel(/Order contact email/);
    await expect(deliveryEmail).toHaveAttribute("required", "");
    await expect(deliveryEmail).toHaveAttribute("aria-required", "true");
    await deliveryEmail.fill("invalid-address");
    await deliveryEmail.blur();
    await expect(deliveryEmail).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByRole("alert")).toHaveText(/valid contact email/);
    await expect(page.getByText(/Automated confirmation email is not currently enabled/)).toBeVisible();
    await deliveryEmail.fill("customer@example.test");
    await expect(deliveryEmail).toHaveAttribute("aria-invalid", "false");
    await page.getByLabel(/I explicitly consent/).check();

    // Ambiguous network, 5xx, and malformed-success retries retain one exact request binding.
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    await expect(page.getByText(/Retry will reuse the exact request and idempotency key/)).toBeVisible();
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    await expect(page.getByText(/request failed \(503\).*Retry will reuse/s)).toBeVisible();
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    await expect(page.getByText(/not Stripe's exact secure host/)).toBeVisible();
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    await expect(page.getByText(/unexpected checkout response/)).toBeVisible();
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    await expect(page.getByText(/request failed \(422\)/)).toBeVisible();

    await page.unrouteAll({ behavior: "wait" });
    await fulfillOptions(page);
    await page.route("**/v1/checkout", async (route) => {
      const request = route.request();
      checkoutRequest = { headers: request.headers(), body: request.postDataJSON(), url: request.url() };
      checkoutRequests.push(checkoutRequest);
      await route.fulfill({
        status: 201,
        headers: privateHeaders,
        body: JSON.stringify({
          order_reference: orderReference,
          status_token: token,
          checkout_url: checkoutUrl,
          payment_state: "checkout_open",
          fulfillment_state: "awaiting_payment",
        }),
      });
    });
    await page.getByRole("button", { name: "Create server-priced test checkout" }).click();
    const stripeLink = page.getByRole("link", { name: "Continue to Stripe sandbox" });
    await expect(page.getByText("Recovery saved?")).toBeVisible();
    await expect(stripeLink).toHaveAttribute("href", checkoutUrl);
    await expect(stripeLink).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(checkoutRequest?.url).not.toContain(token);
    expect(checkoutRequest?.headers["idempotency-key"]).toMatch(/^[A-Za-z0-9_-]{22,86}$/);
    expect(Object.keys(checkoutRequest?.body ?? {}).sort()).toEqual(["certificate_reference", "consent", "email", "manifest_sha256"]);
    expect(checkoutRequest?.body).toMatchObject({
      certificate_reference: "TEST-COA-0001",
      email: "customer@example.test",
      consent: { managed_timestamp: true, terms_version: "phase0-v1", privacy_version: "phase0-v1" },
    });
    expect((checkoutRequest?.body.manifest_sha256 as string)).toMatch(/^[0-9a-f]{64}$/);
    expect(new Set(checkoutRequests.slice(0, 5).map((request) => request.headers["idempotency-key"])).size).toBe(1);
    expect(new Set(checkoutRequests.slice(0, 5).map((request) => JSON.stringify(request.body))).size).toBe(1);
    const ambiguousAcceptedTimes = checkoutRequests.slice(0, 5).map((request) => ((request.body.consent as { accepted_at: string }).accepted_at));
    expect(new Set(ambiguousAcceptedTimes).size).toBe(1);
    expect(checkoutRequests.at(-1)?.headers["idempotency-key"]).not.toBe(checkoutRequests[0].headers["idempotency-key"]);

    const apiBase = new URL(`${process.env.VITE_TIMESTAMP_API_URL!.replace(/\/+$/, "")}/`).href;
    const savedBeforeReload = JSON.parse((await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0")))!);
    expect(savedBeforeReload).toMatchObject({ apiBase, checkoutUrl });
    expect(savedBeforeReload).not.toHaveProperty("apiOrigin");

    let paymentProcessing = false;
    await page.route("**/v1/orders/status", async (route) => {
      const submitted = checkoutRequest!.body as { manifest_sha256: string };
      await route.fulfill({
        status: 200,
        headers: privateHeaders,
        body: JSON.stringify({
          order_reference: orderReference,
          certificate_reference: "TEST-COA-0001",
          manifest_sha256: submitted.manifest_sha256,
          payment_state: paymentProcessing ? "processing" : "checkout_open",
          fulfillment_state: "awaiting_payment",
          created_at: "2026-07-30T12:00:00Z",
          updated_at: "2026-07-30T12:01:00Z",
          proof_available: false,
        }),
      });
    });
    await page.reload();
    await expect(page.getByRole("link", { name: "Continue to Stripe sandbox" })).toBeVisible();
    paymentProcessing = true;
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByRole("link", { name: "Continue to Stripe sandbox" })).toHaveCount(0);
    const savedAfterProcessing = JSON.parse((await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0")))!);
    expect(savedAfterProcessing).not.toHaveProperty("checkoutUrl");
  });

  test("rejects same-origin base confusion and tampered checkout sessions before token transmission", async ({ page }) => {
    let authenticatedRequests = 0;
    await page.route("**/v1/**", async (route) => {
      if (route.request().headers().authorization) authenticatedRequests += 1;
      await route.abort();
    });
    await page.addInitScript(({ storedToken, storedDigest, storedOrder, checkoutUrl }) => {
      if (sessionStorage.getItem("timestamp-test-initialized")) return;
      sessionStorage.setItem("timestamp-test-initialized", "true");
      sessionStorage.setItem("spacerocks.timestamp.phase0", JSON.stringify({
        token: storedToken,
        orderRef: storedOrder,
        certificateReference: "TEST-COA-0001",
        manifestSha256: storedDigest,
        apiBase: "http://127.0.0.1:4400/other-api/",
        apiVersion: "phase0-2026-07-30",
        checkoutUrl,
      }));
    }, { storedToken: token, storedDigest: digest, storedOrder: orderReference, checkoutUrl });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toHaveCount(0);
    await page.waitForTimeout(300);
    expect(authenticatedRequests).toBe(0);

    await page.evaluate(({ storedToken, storedDigest, storedOrder, hostileCheckoutUrl }) => {
      sessionStorage.setItem("spacerocks.timestamp.phase0", JSON.stringify({
        token: storedToken,
        orderRef: storedOrder,
        certificateReference: "TEST-COA-0001",
        manifestSha256: storedDigest,
        apiBase: "http://127.0.0.1:4400/api/",
        apiVersion: "phase0-2026-07-30",
        checkoutUrl: hostileCheckoutUrl,
      }));
    }, {
      storedToken: token,
      storedDigest: digest,
      storedOrder: orderReference,
      hostileCheckoutUrl: `https://checkout.stripe.com.evil.test/c/pay/${checkoutSessionId}#fidkdWxOYHwnPyd1blpxYHZxWjA0SDdUN1NGPEF8`,
    });
    await page.reload();
    await expect(page.getByRole("heading", { name: "Managed blockchain timestamp" })).toHaveCount(0);
    expect(authenticatedRequests).toBe(0);
  });

  test("clears contradictory claims, retains recovery locally, and forgets only on auth rejection", async ({ page }) => {
    let responseMode: "verified_preparing" | "verified_ready" | "stale_stamping" | "manual" | "revoked" = "verified_preparing";
    await page.route("**/v1/orders/status", async (route) => {
      if (responseMode === "revoked") {
        await route.fulfill({ status: 401, headers: privateHeaders, body: JSON.stringify({ detail: "revoked" }) });
        return;
      }
      const verified = {
        ...pendingStatus,
        fulfillment_state: "bitcoin_verified",
        bitcoin_verified_at: "2026-07-30T15:10:00Z",
      };
      await route.fulfill({
        status: 200,
        headers: privateHeaders,
        body: JSON.stringify(responseMode === "verified_preparing"
          ? { ...verified, proof_available: false }
          : responseMode === "stale_stamping"
            ? { ...pendingStatus, fulfillment_state: "stamping", proof_available: true, calendar_submitted_at: undefined }
          : responseMode === "manual"
            ? { ...pendingStatus, payment_state: "failed", fulfillment_state: "manual_review", proof_available: false, calendar_submitted_at: undefined }
            : verified),
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Recover a timestamp order" }).click();
    await page.getByLabel("Private recovery code").fill(token);
    await page.getByRole("button", { name: "Recover in this tab" }).click();
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toBeVisible();
    await expect(page.getByText(/downloadable proof bundle is still being prepared/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Download proof bundle" })).toBeDisabled();
    await expect(page.getByText(/Bitcoin verification metadata is current/)).toBeVisible();

    responseMode = "verified_ready";
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByRole("button", { name: "Download proof bundle" })).toBeEnabled();

    responseMode = "stale_stamping";
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByText(/state without a downloadable bundle/)).toBeVisible();
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Download proof bundle" })).toBeDisabled();
    expect(await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0"))).not.toBeNull();

    responseMode = "manual";
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByText("Manual review")).toBeVisible();
    await expect(page.getByText("Service verification time:")).toHaveCount(0);
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Download proof bundle" })).toBeDisabled();

    responseMode = "revoked";
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByText(/removed from this tab/)).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0"))).toBeNull();
  });

  test("blocks malicious Stripe hosts and revoked recovery codes", async ({ page }) => {
    let statusAttempt = 0;
    await page.route("**/v1/orders/status", async (route) => {
      statusAttempt += 1;
      if (statusAttempt === 1) {
        await route.fulfill({ status: 200, headers: privateHeaders, body: JSON.stringify({ padding: "x".repeat(70 * 1024) }) });
        return;
      }
      await route.fulfill({ status: 401, headers: privateHeaders, body: JSON.stringify({ detail: "revoked" }) });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Recover a timestamp order" }).click();
    await page.getByLabel("Private recovery code").fill(token);
    await page.getByRole("button", { name: "Recover in this tab" }).click();
    await expect(page.getByText(/response is too large/)).toBeVisible();
    await page.getByRole("button", { name: "Recover in this tab" }).click();
    await expect(page.getByText(/invalid, expired, or revoked/)).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem("spacerocks.timestamp.phase0"))).toBeNull();
  });
});

test.describe("configured production timestamp service", () => {
  test.skip(!productionConfigured, "Run with a complete production timestamp frontend configuration at server start.");

  test("renders production policy links and precise two-stage confirmation copy", async ({ page }) => {
    await fulfillCheckoutPrice(page);
    await page.route("**/v1/orders/status", async (route) => {
      await route.fulfill({
        status: 200,
        headers: privateHeaders,
        body: JSON.stringify({
          ...pendingStatus,
          fulfillment_state: "bitcoin_verified",
          bitcoin_verified_at: "2026-08-27T15:10:00Z",
        }),
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Recover a timestamp order" }).click();
    const panel = page.locator("#timestamp-service");
    await expect(panel.getByText("Live paid service")).toBeVisible();
    await expect(panel).not.toContainText(/sandbox|future fee|phase 0|\btest\b/i);
    await expect(panel.getByRole("link", { name: "Terms" })).toHaveAttribute("href", process.env.VITE_TIMESTAMP_TERMS_URL!);
    await expect(panel.getByRole("link", { name: "Privacy" })).toHaveAttribute("href", process.env.VITE_TIMESTAMP_PRIVACY_URL!);
    await expect(panel.getByRole("link", { name: "Refund Policy" })).toHaveAttribute("href", process.env.VITE_TIMESTAMP_REFUND_URL!);
    await expect(panel.getByRole("link", { name: process.env.VITE_TIMESTAMP_SUPPORT_EMAIL! })).toHaveAttribute(
      "href",
      `mailto:${process.env.VITE_TIMESTAMP_SUPPORT_EMAIL!}`,
    );
    await page.getByLabel("Private recovery code").fill(token);
    await page.getByRole("button", { name: "Recover in this tab" }).click();
    await expect(page.getByText("Initial Bitcoin confirmation verified")).toBeVisible();
    const verifiedStatus = page.locator(".timestamp-status");
    await expect(verifiedStatus).toContainText(/at least one Bitcoin confirmation/);
    await expect(verifiedStatus).toContainText(/subject to reorganization monitoring/);
    await expect(verifiedStatus).toContainText(/private recovery code.*later proof upgrades/i);
    await expect(panel).not.toContainText(/current confirmation count|irreversible finality/i);
  });
});
