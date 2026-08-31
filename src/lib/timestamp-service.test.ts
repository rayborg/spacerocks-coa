import { describe, expect, it, vi } from "vitest";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import orderStatusSchema from "../../contracts/schemas/order-status.schema.json";
import {
  checkoutAttemptMatches,
  createCheckoutAttempt,
  createIdempotencyKey,
  createTimestampService,
  forgetTimestampSession,
  loadTimestampSession,
  parseTimestampServiceConfig,
  pollOrderStatus,
  saveTimestampSession,
  TIMESTAMP_CONTRACT_VERSION,
  TIMESTAMP_SESSION_KEY,
  type TimestampSession,
} from "./timestamp-service";

const digest = "cf0a31b01661599b8f73cd2dd2830f859e36a00c8ca22b259b33a7ec32c067cc";
const token = "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const replacementToken = "v1.BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
const orderReference = "ts_01K1E2Q3R4S5T6V7W8X9Y0Z1AB";
const fidFragment = "fidkdWxOYHwnPyd1blpxYHZxWjA0SDdUN1NGPEF8";
const testSessionId = "cs_test_a1SafeSession9";
const liveSessionId = "cs_live_a1SafeSession9";
const testCheckoutUrl = `https://checkout.stripe.com/c/pay/${testSessionId}#${fidFragment}`;
const liveCheckoutUrl = `https://checkout.stripe.com/c/pay/${liveSessionId}#${fidFragment}`;
const checkoutFixture = {
  order_reference: orderReference,
  status_token: token,
  checkout_url: testCheckoutUrl,
  payment_state: "checkout_open",
  fulfillment_state: "awaiting_payment",
};
const statusFixture = {
  order_reference: orderReference,
  certificate_reference: "AZ-2019-0447-HE",
  manifest_sha256: digest,
  payment_state: "paid",
  fulfillment_state: "calendar_pending",
  created_at: "2026-07-30T12:00:00Z",
  updated_at: "2026-07-30T12:05:00Z",
  calendar_submitted_at: "2026-07-30T12:05:00Z",
  proof_available: true,
  message_code: "bitcoin_confirmation_pending",
};
const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateOrderStatusSchema = ajv.compile(orderStatusSchema);

function jsonResponse(value: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json");
  headers.set("cache-control", "no-store");
  return new Response(JSON.stringify(value), { ...init, headers });
}

function config() {
  const parsed = parseTimestampServiceConfig("https://timestamp.example.test/api", false);
  if (!parsed) throw new Error("test config failed");
  return parsed;
}

function productionConfig() {
  const parsed = parseTimestampServiceConfig("https://timestamp.example.com/api", false, {
    mode: "production",
    policyVersion: "2026-08-v1",
    termsUrl: "https://www.example.com/legal/terms",
    privacyUrl: "https://www.example.com/legal/privacy",
    refundUrl: "https://www.example.com/legal/refunds",
    supportEmail: "support@example.com",
  });
  if (!parsed) throw new Error("production test config failed");
  return parsed;
}

function checkoutAttempt(overrides: Partial<{ certificateReference: string; manifestSha256: string; email: string; policyVersion: string; acceptedAt: string }> = {}) {
  return createCheckoutAttempt({
    certificateReference: "AZ-2019-0447-HE",
    manifestSha256: digest,
    email: "customer@example.test",
    policyVersion: "phase0-v1",
    acceptedAt: "2026-07-30T12:00:00Z",
    ...overrides,
  });
}

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("timestamp service configuration", () => {
  it("requires explicit secure configuration and preserves a base path", () => {
    expect(parseTimestampServiceConfig(undefined, false)).toBeUndefined();
    expect(parseTimestampServiceConfig("http://timestamp.example.test", false)).toBeUndefined();
    expect(parseTimestampServiceConfig("https://user:pass@timestamp.example.test", false)).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.test?token=x", false)).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.test/#x", false)).toBeUndefined();
    expect(parseTimestampServiceConfig("http://127.0.0.1:8000/root", true)?.baseUrl).toBe("http://127.0.0.1:8000/root/");
    expect(parseTimestampServiceConfig("http://localhost.example.com", true)).toBeUndefined();
    expect(config()).toMatchObject({
      baseUrl: "https://timestamp.example.test/api/",
      apiOrigin: "https://timestamp.example.test",
      mode: "sandbox",
      policyVersion: "phase0-v1",
    });
  });

  it("requires complete safe public policy metadata for production mode", () => {
    const production = {
      mode: "production",
      policyVersion: "2026-08-v1",
      termsUrl: "https://www.example.com/legal/terms",
      privacyUrl: "https://www.example.com/legal/privacy",
      refundUrl: "https://www.example.com/legal/refunds",
      supportEmail: "support@example.com",
    };
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, production)).toMatchObject({
      mode: "production",
      policyVersion: "2026-08-v1",
      termsUrl: "https://www.example.com/legal/terms",
      privacyUrl: "https://www.example.com/legal/privacy",
      refundUrl: "https://www.example.com/legal/refunds",
      supportEmail: "support@example.com",
    });
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, supportEmail: undefined })).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, policyVersion: "phase0-v2" })).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, termsUrl: "http://www.example.com/terms" })).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, privacyUrl: "https://user:pass@www.example.com/privacy" })).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, refundUrl: "https://www.example.com/refunds?order=1" })).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.com/api", false, { ...production, mode: "live" })).toBeUndefined();
  });

  it("rejects raw and encoded path traversal before URL normalization", () => {
    for (const unsafe of [
      "https://timestamp.example.test/api/./private",
      "https://timestamp.example.test/api/../private",
      "https://timestamp.example.test/api/%2e/private",
      "https://timestamp.example.test/api/.%2e/private",
      "https://timestamp.example.test/api/%2e./private",
      "https://timestamp.example.test/api/%2E%2e/private",
      "https://timestamp.example.test/api/%252e%252e/private",
      "https://timestamp.example.test/api%2fprivate",
      "https://timestamp.example.test/api%5cprivate",
      "https://timestamp.example.test/api\\private",
      "https://timestamp.example.test/api/%2e%2fprivate",
    ]) expect(parseTimestampServiceConfig(unsafe, false), unsafe).toBeUndefined();
    expect(parseTimestampServiceConfig("https://timestamp.example.test/api.v1/path", false)?.baseUrl)
      .toBe("https://timestamp.example.test/api.v1/path/");
  });

  it("creates independent 128-bit base64url idempotency keys", () => {
    const first = createIdempotencyKey();
    const second = createIdempotencyKey();
    expect(first).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(second).not.toBe(first);
  });
});

describe("timestamp service requests", () => {
  it("loads and strictly validates the server-controlled checkout price", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ amount_minor: 999, currency: "usd" }));
    const service = createTimestampService(config(), fetcher);

    await expect(service.getCheckoutPrice()).resolves.toEqual({ amountMinor: 999, currency: "usd" });
    expect(fetcher).toHaveBeenCalledWith("https://timestamp.example.test/api/v1/checkout/price", expect.objectContaining({
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    }));

    for (const invalid of [
      { amount_minor: 0, currency: "usd" },
      { amount_minor: 999.5, currency: "usd" },
      { amount_minor: 999, currency: "USD" },
      { amount_minor: 999, currency: "isk" },
      { amount_minor: 999, currency: "usd", price_id: "price_private" },
    ]) {
      const invalidFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(invalid));
      await expect(createTimestampService(config(), invalidFetcher).getCheckoutPrice()).rejects.toThrow(/checkout price/);
    }
  });

  it("sends only the allowlisted checkout body and a random idempotency header", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(checkoutFixture, { status: 201 }));
    const service = createTimestampService(config(), fetcher);
    await service.createCheckout(checkoutAttempt({ email: " customer@example.test " }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("https://timestamp.example.test/api/v1/checkout");
    expect(init?.credentials).toBe("omit");
    expect(init?.referrerPolicy).toBe("no-referrer");
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(JSON.parse(String(init?.body))).toEqual({
      certificate_reference: "AZ-2019-0447-HE",
      manifest_sha256: digest,
      email: "customer@example.test",
      consent: {
        managed_timestamp: true,
        terms_version: "phase0-v1",
        privacy_version: "phase0-v1",
        accepted_at: "2026-07-30T12:00:00Z",
      },
    });
  });

  it("rejects uppercase digests, oversized email, unknown response fields, and Stripe lookalikes", async () => {
    const service = createTimestampService(config(), vi.fn<typeof fetch>());
    expect(() => checkoutAttempt({ certificateReference: "AZ-1", manifestSha256: digest.toUpperCase(), email: "a@example.test" }))
      .toThrow("lowercase");
    expect(() => checkoutAttempt({ certificateReference: "AZ-1", email: `${"a".repeat(250)}@x.test` }))
      .toThrow("254");

    const unknownFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ...checkoutFixture, email: "leak@example.test" }, { status: 201 }));
    await expect(createTimestampService(config(), unknownFetcher).createCheckout(checkoutAttempt({ certificateReference: "AZ-1", email: "a@example.test" })))
      .rejects.toThrow("unexpected checkout");

    for (const checkoutUrl of [
      "https://checkout.stripe.com.evil.test/pay",
      "https://stripe.com/pay",
      "https://checkout.stripe.com@evil.test/pay",
      "http://checkout.stripe.com/pay",
      `https://checkout.stripe.com:443/c/pay/${testSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}?next=evil#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}/../cs_test_other#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/%2e%2e/${testSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${liveSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#opaqueFragmentValue123`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#fidshort`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#fidInvalid!FragmentValue`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#${`fid${"a".repeat(1537)}`}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#`,
    ]) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ...checkoutFixture, checkout_url: checkoutUrl }, { status: 201 }));
      await expect(createTimestampService(config(), fetcher).createCheckout(checkoutAttempt({ certificateReference: "AZ-1", email: "a@example.test" })))
        .rejects.toThrow(/Stripe|URL/);
    }
  });

  it("accepts realistic mode-bound Stripe URLs with fid or no optional fragment", async () => {
    const sandboxFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(checkoutFixture, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({
        ...checkoutFixture,
        checkout_url: `https://checkout.stripe.com/c/pay/${testSessionId}`,
      }, { status: 201 }));
    const sandboxService = createTimestampService(config(), sandboxFetcher);
    await expect(sandboxService.createCheckout(checkoutAttempt())).resolves.toMatchObject({ checkoutUrl: testCheckoutUrl });
    await expect(sandboxService.createCheckout(checkoutAttempt())).resolves.toMatchObject({
      checkoutUrl: `https://checkout.stripe.com/c/pay/${testSessionId}`,
    });

    const liveFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      ...checkoutFixture,
      checkout_url: liveCheckoutUrl,
    }, { status: 201 }));
    await expect(createTimestampService(productionConfig(), liveFetcher).createCheckout(checkoutAttempt({
      policyVersion: "2026-08-v1",
    }))).resolves.toMatchObject({ checkoutUrl: liveCheckoutUrl });
  });

  it("reuses the exact body, consent time, and key after an ambiguous lost response", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("network response lost"))
      .mockResolvedValueOnce(jsonResponse(checkoutFixture, { status: 201 }));
    const service = createTimestampService(config(), fetcher);
    const attempt = checkoutAttempt();
    await expect(service.createCheckout(attempt)).rejects.toThrow("response lost");
    await expect(service.createCheckout(attempt)).resolves.toMatchObject({ orderReference });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][1]?.body).toBe(fetcher.mock.calls[1][1]?.body);
    expect((fetcher.mock.calls[0][1]?.headers as Record<string, string>)["Idempotency-Key"])
      .toBe((fetcher.mock.calls[1][1]?.headers as Record<string, string>)["Idempotency-Key"]);
    expect(checkoutAttemptMatches(attempt, { certificateReference: "AZ-2019-0447-HE", manifestSha256: digest, email: " customer@example.test ", policyVersion: "phase0-v1" })).toBe(true);
    expect(checkoutAttemptMatches(attempt, { certificateReference: "AZ-2019-0447-HE", manifestSha256: digest, email: "other@example.test", policyVersion: "phase0-v1" })).toBe(false);
    expect(createCheckoutAttempt({ certificateReference: "AZ-2019-0447-HE", manifestSha256: digest, email: "other@example.test", policyVersion: "phase0-v1" }).idempotencyKey)
      .not.toBe(attempt.idempotencyKey);
  });

  it("binds the configured policy version into the immutable checkout attempt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(checkoutFixture, { status: 201 }));
    const attempt = checkoutAttempt({ policyVersion: "2026-08-v1" });
    expect(attempt.policyVersion).toBe("2026-08-v1");
    expect(JSON.parse(attempt.body).consent).toMatchObject({
      terms_version: "2026-08-v1",
      privacy_version: "2026-08-v1",
    });
    await createTimestampService(config(), fetcher).createCheckout(attempt);
    expect(fetcher.mock.calls[0][1]?.body).toBe(attempt.body);
    expect(checkoutAttemptMatches(attempt, {
      certificateReference: "AZ-2019-0447-HE",
      manifestSha256: digest,
      email: "customer@example.test",
      policyVersion: "2026-08-v1",
    })).toBe(true);
    expect(checkoutAttemptMatches(attempt, {
      certificateReference: "AZ-2019-0447-HE",
      manifestSha256: digest,
      email: "customer@example.test",
      policyVersion: "2026-09-v1",
    })).toBe(false);
  });

  it("sends tokens only as bearer headers to fixed no-store endpoints", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse(statusFixture));
    const status = await createTimestampService(config(), fetcher).getStatus(token);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("https://timestamp.example.test/api/v1/orders/status");
    expect(String(url)).not.toContain(token);
    expect(init?.headers).toEqual({ Authorization: `Bearer ${token}`, "Cache-Control": "no-store" });
    expect(init?.cache).toBe("no-store");
    expect(status.fulfillmentState).toBe("calendar_pending");
  });

  it("rejects non-no-store, unknown, oversized, and falsely complete statuses", async () => {
    const noStoreMissing = new Response(JSON.stringify(statusFixture), { headers: { "content-type": "application/json" } });
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(noStoreMissing)).getStatus(token)).rejects.toThrow("no-store");
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ...statusFixture, email: "private@example.test" }))).getStatus(token))
      .rejects.toThrow("unexpected order status");
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      ...statusFixture,
      fulfillment_state: "bitcoin_verified",
    }))).getStatus(token)).rejects.toThrow("incomplete Bitcoin");
    const oversized = new Response("{}", { headers: { "content-type": "application/json", "cache-control": "no-store", "content-length": "70000" } });
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(oversized)).getStatus(token)).rejects.toThrow("too large");
  });

  it("rejects contradictory payment, fulfillment, and timestamp combinations", async () => {
    const bothTimes = {
      calendar_submitted_at: "2026-07-30T12:05:00Z",
      bitcoin_verified_at: "2026-07-30T15:10:00Z",
    };
    const contradictions = [
      { payment_state: "checkout_open", fulfillment_state: "bitcoin_verified", ...bothTimes },
      { payment_state: "processing", fulfillment_state: "calendar_pending", calendar_submitted_at: bothTimes.calendar_submitted_at },
      { payment_state: "failed", fulfillment_state: "delivered", ...bothTimes },
      { payment_state: "expired", fulfillment_state: "queued" },
      { payment_state: "paid", fulfillment_state: "awaiting_payment" },
      { payment_state: "paid", fulfillment_state: "calendar_pending", calendar_submitted_at: undefined },
      { payment_state: "paid", fulfillment_state: "calendar_pending", ...bothTimes },
      { payment_state: "paid", fulfillment_state: "bitcoin_verified", calendar_submitted_at: undefined, bitcoin_verified_at: bothTimes.bitcoin_verified_at },
      { payment_state: "paid", fulfillment_state: "delivered", bitcoin_verified_at: undefined },
      { payment_state: "paid", fulfillment_state: "queued", calendar_submitted_at: bothTimes.calendar_submitted_at },
      { payment_state: "paid", fulfillment_state: "stamping", bitcoin_verified_at: bothTimes.bitcoin_verified_at },
    ];
    for (const changes of contradictions) {
      const fixture = { ...statusFixture, ...changes };
      for (const key of ["calendar_submitted_at", "bitcoin_verified_at"] as const) {
        if (fixture[key] === undefined) delete fixture[key];
      }
      await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture))).getStatus(token), JSON.stringify(changes))
        .rejects.toThrow(/contradictory|timestamps|pending-calendar|incomplete|without calendar|Bitcoin verification time/);
    }
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      ...statusFixture,
      payment_state: "failed",
      fulfillment_state: "manual_review",
      proof_available: false,
      calendar_submitted_at: undefined,
    }))).getStatus(token)).resolves.toMatchObject({ fulfillmentState: "manual_review" });
  });

  it("matches artifact availability and current Bitcoin-time rules", async () => {
    const bitcoinTime = "2026-07-30T15:10:00Z";
    const invalidStatuses = [
      { ...statusFixture, proof_available: false },
      { ...statusFixture, payment_state: "failed", fulfillment_state: "manual_review", proof_available: true },
      { ...statusFixture, payment_state: "failed", fulfillment_state: "manual_review", proof_available: false, bitcoin_verified_at: bitcoinTime },
      { ...statusFixture, payment_state: "checkout_open", fulfillment_state: "awaiting_payment", proof_available: true, calendar_submitted_at: undefined },
      { ...statusFixture, payment_state: "paid", fulfillment_state: "queued", proof_available: true, calendar_submitted_at: undefined },
      { ...statusFixture, payment_state: "paid", fulfillment_state: "stamping", proof_available: true, calendar_submitted_at: undefined },
      { ...statusFixture, fulfillment_state: "delivered", bitcoin_verified_at: bitcoinTime, proof_available: false },
    ];
    for (const candidate of invalidStatuses) {
      const fixture = { ...candidate };
      if (fixture.calendar_submitted_at === undefined) delete fixture.calendar_submitted_at;
      await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixture))).getStatus(token))
        .rejects.toThrow(/available proof|downloadable bundle|Bitcoin verification time|timestamps/);
    }

    const validManualReview = {
      ...statusFixture,
      payment_state: "failed",
      fulfillment_state: "manual_review",
      proof_available: false,
      calendar_submitted_at: undefined,
    };
    delete validManualReview.calendar_submitted_at;
    expect(validateOrderStatusSchema(validManualReview), JSON.stringify(validateOrderStatusSchema.errors)).toBe(true);
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(validManualReview))).getStatus(token))
      .resolves.toMatchObject({ fulfillmentState: "manual_review", proofAvailable: false, bitcoinVerifiedAt: undefined });

    const manualReviewWithHistoricalCalendar = {
      ...statusFixture,
      payment_state: "failed",
      fulfillment_state: "manual_review",
      proof_available: false,
    };
    expect(validateOrderStatusSchema(manualReviewWithHistoricalCalendar)).toBe(false);
    await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(manualReviewWithHistoricalCalendar))).getStatus(token))
      .rejects.toThrow(/timestamps/);

    for (const proofAvailable of [false, true]) {
      const verified = {
        ...statusFixture,
        fulfillment_state: "bitcoin_verified",
        bitcoin_verified_at: bitcoinTime,
        proof_available: proofAvailable,
      };
      await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(verified))).getStatus(token))
        .resolves.toMatchObject({ fulfillmentState: "bitcoin_verified", proofAvailable, bitcoinVerifiedAt: bitcoinTime });
    }

    const verifiedWithoutBundle = {
      ...statusFixture,
      fulfillment_state: "bitcoin_verified",
      bitcoin_verified_at: bitcoinTime,
      proof_available: false,
    };
    const stampingWithStaleProof = {
      ...statusFixture,
      fulfillment_state: "stamping",
      calendar_submitted_at: undefined,
      proof_available: true,
    };
    delete stampingWithStaleProof.calendar_submitted_at;
    const schemaAllowsVerifiedWithoutBundle = validateOrderStatusSchema(verifiedWithoutBundle);
    const schemaAllowsStaleStampingProof = validateOrderStatusSchema(stampingWithStaleProof);
    const desiredMatrix = schemaAllowsVerifiedWithoutBundle && !schemaAllowsStaleStampingProof;
    const legacyMatrix = !schemaAllowsVerifiedWithoutBundle && schemaAllowsStaleStampingProof;
    expect(desiredMatrix || legacyMatrix, JSON.stringify(validateOrderStatusSchema.errors)).toBe(true);
  });

  it("maps revoked tokens without exposing them and validates rotation", async () => {
    const revokedFetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 401 }));
    await expect(createTimestampService(config(), revokedFetcher).getStatus(token)).rejects.toThrow("revoked");
    expect(revokedFetcher.mock.calls[0][0]).not.toContain(token);

    const rotateFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status_token: replacementToken }));
    await expect(createTimestampService(config(), rotateFetcher).rotateToken(token)).resolves.toBe(replacementToken);
    expect(rotateFetcher.mock.calls[0][0]).toBe("https://timestamp.example.test/api/v1/orders/rotate-token");
    expect(rotateFetcher.mock.calls[0][1]?.method).toBe("POST");
  });

  it("bounds polling and supports an already-aborted signal", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(statusFixture));
    await pollOrderStatus(createTimestampService(config(), fetcher), token, { maxAttempts: 1 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const controller = new AbortController();
    controller.abort();
    await expect(pollOrderStatus(createTimestampService(config(), fetcher), token, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("recovery storage and proof downloads", () => {
  const session: TimestampSession = {
    token,
    orderRef: orderReference,
    certificateReference: "AZ-2019-0447-HE",
    manifestSha256: digest,
    apiBase: "https://timestamp.example.test/api/",
    apiVersion: TIMESTAMP_CONTRACT_VERSION,
    checkoutUrl: testCheckoutUrl,
  };

  it("binds sessions to the exact API base and validates persisted checkout continuation", () => {
    const storage = new MemoryStorage();
    saveTimestampSession(session, config(), storage);
    expect(Object.keys(JSON.parse(storage.getItem(TIMESTAMP_SESSION_KEY)!)).sort()).toEqual([
      "apiBase", "apiVersion", "certificateReference", "checkoutUrl", "manifestSha256", "orderRef", "token",
    ]);
    expect(loadTimestampSession(config(), storage)).toEqual(session);
    expect(loadTimestampSession(parseTimestampServiceConfig("https://other.example.test", false)!, storage)).toBeUndefined();
    saveTimestampSession(session, config(), storage);
    expect(loadTimestampSession(parseTimestampServiceConfig("https://timestamp.example.test:444/api", false)!, storage)).toBeUndefined();
    saveTimestampSession(session, config(), storage);
    expect(loadTimestampSession(parseTimestampServiceConfig("https://timestamp.example.test/other-api", false)!, storage)).toBeUndefined();
    storage.setItem(TIMESTAMP_SESSION_KEY, JSON.stringify({ ...session, apiBase: "http://timestamp.example.test/api/" }));
    expect(loadTimestampSession(config(), storage)).toBeUndefined();
    for (const checkoutUrl of [
      `https://checkout.stripe.com.evil.test/c/pay/${testSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/pay/${testSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/../${testSessionId}#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}?token=x#${fidFragment}`,
      `https://checkout.stripe.com/c/pay/${testSessionId}#x`,
      liveCheckoutUrl,
    ]) {
      storage.setItem(TIMESTAMP_SESSION_KEY, JSON.stringify({ ...session, checkoutUrl }));
      expect(loadTimestampSession(config(), storage), checkoutUrl).toBeUndefined();
    }
    storage.setItem(TIMESTAMP_SESSION_KEY, JSON.stringify({ ...session, apiOrigin: "https://timestamp.example.test", apiBase: undefined }));
    expect(loadTimestampSession(config(), storage)).toBeUndefined();
    storage.setItem(TIMESTAMP_SESSION_KEY, JSON.stringify({ ...session, email: "must-not-persist@example.test" }));
    expect(loadTimestampSession(config(), storage)).toBeUndefined();
    forgetTimestampSession(storage);
    expect(storage.getItem(TIMESTAMP_SESSION_KEY)).toBeNull();
  });

  it("uses a sanitized local filename after validating a safe server filename", async () => {
    const response = new Response(new Uint8Array([1, 2, 3]), {
      headers: {
        "content-type": "application/zip",
        "content-disposition": 'attachment; filename="timestamp-proof.zip"',
        "cache-control": "private, no-store",
      },
    });
    const proof = await createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(response))
      .downloadProof(token, "AZ:2019/0447");
    expect(proof.fileName).toBe("AZ-2019-0447-bitcoin-timestamp.zip");
    expect(proof.fileName).not.toContain(token);
    expect(proof.blob.size).toBe(3);
  });

  it("rejects unsafe proof types, dispositions, and declared sizes", async () => {
    for (const response of [
      new Response("<html>", { headers: { "content-type": "text/html", "content-disposition": "attachment", "cache-control": "no-store" } }),
      new Response("proof", { headers: { "content-type": "application/zip", "content-disposition": "inline", "cache-control": "no-store" } }),
      new Response("proof", { headers: { "content-type": "application/zip", "content-disposition": 'attachment; filename="../../token.zip"', "cache-control": "no-store" } }),
      new Response("proof", { headers: { "content-type": "application/zip", "content-disposition": "attachment; filename*=UTF-8''proof.zip", "cache-control": "no-store" } }),
      new Response("proof", { headers: { "content-type": "application/zip", "content-disposition": "attachment", "content-length": String(13 * 1024 * 1024), "cache-control": "no-store" } }),
    ]) {
      await expect(createTimestampService(config(), vi.fn<typeof fetch>().mockResolvedValue(response)).downloadProof(token, "AZ-1"))
        .rejects.toThrow(/unsafe|large/);
    }
  });
});
