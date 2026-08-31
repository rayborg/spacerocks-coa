export const TIMESTAMP_CONTRACT_VERSION = "phase0-2026-07-30";
export const TIMESTAMP_POLICY_VERSION = "phase0-v1";
export const TIMESTAMP_SESSION_KEY = "spacerocks.timestamp.phase0";

const MAX_JSON_BYTES = 64 * 1024;
const MAX_PROOF_BYTES = 12 * 1024 * 1024;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const CERTIFICATE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const ORDER_PATTERN = /^ts_[0-9A-HJKMNP-TV-Z]{26}$/;
const TOKEN_PATTERN = /^v[1-9][0-9]*\.[A-Za-z0-9_-]{43}$/;
const IDEMPOTENCY_PATTERN = /^[A-Za-z0-9_-]{22,86}$/;
const MESSAGE_PATTERN = /^[a-z0-9_]{1,64}$/;
const POLICY_VERSION_PATTERN = /^[A-Za-z0-9._-]{1,32}$/;
const CHECKOUT_SESSION_PATTERN = /^cs_(test|live)_[A-Za-z0-9]{1,500}$/;
const CHECKOUT_FRAGMENT_PATTERN = /^fid[A-Za-z0-9._~-]{16,1536}$/;

export type PaymentState = "checkout_open" | "processing" | "paid" | "failed" | "expired" | "refunded" | "disputed";
export type FulfillmentState = "awaiting_payment" | "queued" | "stamping" | "calendar_pending" | "bitcoin_verified" | "delivered" | "manual_review";

interface TimestampServiceConfigBase {
  baseUrl: string;
  apiOrigin: string;
  contractVersion: typeof TIMESTAMP_CONTRACT_VERSION;
}

export type TimestampServiceConfig = TimestampServiceConfigBase & ({
  mode: "sandbox";
  policyVersion: typeof TIMESTAMP_POLICY_VERSION;
} | {
  mode: "production";
  policyVersion: string;
  termsUrl: string;
  privacyUrl: string;
  refundUrl: string;
  supportEmail: string;
});

export interface TimestampServicePublicEnvironment {
  mode?: string;
  policyVersion?: string;
  termsUrl?: string;
  privacyUrl?: string;
  refundUrl?: string;
  supportEmail?: string;
}

export interface CheckoutRequest {
  certificateReference: string;
  manifestSha256: string;
  email: string;
  policyVersion: string;
  acceptedAt?: string;
}

export interface CheckoutAttempt {
  idempotencyKey: string;
  body: string;
  certificateReference: string;
  manifestSha256: string;
  email: string;
  policyVersion: string;
  acceptedAt: string;
}

export interface CheckoutResponse {
  orderReference: string;
  statusToken: string;
  checkoutUrl: string;
  paymentState: "checkout_open";
  fulfillmentState: "awaiting_payment";
}

export interface OrderStatus {
  orderReference: string;
  certificateReference: string;
  manifestSha256: string;
  paymentState: PaymentState;
  fulfillmentState: FulfillmentState;
  createdAt: string;
  updatedAt: string;
  calendarSubmittedAt?: string;
  bitcoinVerifiedAt?: string;
  proofAvailable: boolean;
  messageCode?: string;
}

export interface TimestampSession {
  token: string;
  orderRef: string;
  certificateReference: string;
  manifestSha256: string;
  apiBase: string;
  apiVersion: typeof TIMESTAMP_CONTRACT_VERSION;
  checkoutUrl?: string;
}

export interface ProofDownload {
  blob: Blob;
  fileName: string;
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: JsonRecord, required: string[], optional: string[] = []): boolean {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => Object.prototype.hasOwnProperty.call(value, key))
    && keys.every((key) => allowed.has(key));
}

function isDateTime(value: unknown): value is string {
  return typeof value === "string"
    && value.length <= 40
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function isLoopback(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

function hasUnsafeRawPath(rawUrl: string): boolean {
  if (/[\\\u0000-\u001f\u007f]/.test(rawUrl) || /%(?:2f|5c)/i.test(rawUrl)) return true;
  const match = /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^/?#]*(\/[^?#]*)?/.exec(rawUrl);
  if (!match) return true;
  const rawPath = match[1] ?? "/";
  return rawPath.split("/").some((segment) => {
    const dotsDecoded = segment.replace(/%2e/gi, ".");
    return dotsDecoded === "." || dotsDecoded === ".." || /%25(?:2e|2f|5c)/i.test(segment);
  });
}

export function parseTimestampServiceConfig(
  rawUrl: string | undefined,
  allowLoopbackHttp = import.meta.env.DEV || import.meta.env.MODE === "test",
  environment: TimestampServicePublicEnvironment = {},
): TimestampServiceConfig | undefined {
  if (!rawUrl || rawUrl.trim() !== rawUrl || rawUrl.length > 2048) return undefined;
  if (hasUnsafeRawPath(rawUrl)) return undefined;
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return undefined;
  }
  if (url.username || url.password || url.search || url.hash) return undefined;
  if (url.protocol !== "https:" && !(allowLoopbackHttp && url.protocol === "http:" && isLoopback(url.hostname))) return undefined;
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/`;
  const base: TimestampServiceConfigBase = {
    baseUrl: url.href,
    apiOrigin: url.origin,
    contractVersion: TIMESTAMP_CONTRACT_VERSION,
  };
  const mode = environment.mode ?? "sandbox";
  if (mode === "sandbox") {
    return { ...base, mode, policyVersion: TIMESTAMP_POLICY_VERSION };
  }
  if (mode !== "production" || !environment.policyVersion
    || !environment.termsUrl || !environment.privacyUrl || !environment.refundUrl || !environment.supportEmail) {
    return undefined;
  }
  const policyVersion = validatePolicyVersion(environment.policyVersion);
  if (!policyVersion || policyVersion.toLowerCase().startsWith("phase0")) return undefined;
  const termsUrl = parsePublicPolicyUrl(environment.termsUrl);
  const privacyUrl = parsePublicPolicyUrl(environment.privacyUrl);
  const refundUrl = parsePublicPolicyUrl(environment.refundUrl);
  const supportEmail = parseSupportEmail(environment.supportEmail);
  if (!termsUrl || !privacyUrl || !refundUrl || !supportEmail) return undefined;
  return { ...base, mode, policyVersion, termsUrl, privacyUrl, refundUrl, supportEmail };
}

function validatePolicyVersion(value: string): string | undefined {
  return value.trim() === value && POLICY_VERSION_PATTERN.test(value) ? value : undefined;
}

function parsePublicPolicyUrl(value: string): string | undefined {
  if (value.trim() !== value || value.length > 2048 || hasUnsafeRawPath(value)) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) return undefined;
    return url.href;
  } catch {
    return undefined;
  }
}

function parseSupportEmail(value: string): string | undefined {
  if (value.trim() !== value || value.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return undefined;
  return value;
}

export const timestampServiceConfig = parseTimestampServiceConfig(
  import.meta.env.VITE_TIMESTAMP_API_URL,
  undefined,
  {
    mode: import.meta.env.VITE_TIMESTAMP_SERVICE_MODE,
    policyVersion: import.meta.env.VITE_TIMESTAMP_POLICY_VERSION,
    termsUrl: import.meta.env.VITE_TIMESTAMP_TERMS_URL,
    privacyUrl: import.meta.env.VITE_TIMESTAMP_PRIVACY_URL,
    refundUrl: import.meta.env.VITE_TIMESTAMP_REFUND_URL,
    supportEmail: import.meta.env.VITE_TIMESTAMP_SUPPORT_EMAIL,
  },
);

function endpoint(config: TimestampServiceConfig, path: string): string {
  if (!/^v1\/[a-z/-]+$/.test(path)) throw new Error("Invalid timestamp service endpoint.");
  const url = new URL(path, config.baseUrl);
  const basePath = new URL(config.baseUrl).pathname;
  if (url.origin !== config.apiOrigin || !url.pathname.startsWith(basePath)) {
    throw new Error("Timestamp service endpoint escaped its configured base.");
  }
  return url.href;
}

function randomBase64Url(bytes = 16): string {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function createIdempotencyKey(): string {
  const key = randomBase64Url(16);
  if (!IDEMPOTENCY_PATTERN.test(key)) throw new Error("Secure idempotency key generation failed.");
  return key;
}

export function validateTimestampEmail(email: string): string {
  const normalized = email.trim();
  if (normalized.length < 3 || normalized.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) {
    throw new Error("Enter a valid email address of 254 characters or fewer.");
  }
  return normalized;
}

export function isValidTimestampEmail(email: string): boolean {
  try {
    validateTimestampEmail(email);
    return true;
  } catch {
    return false;
  }
}

function validateCertificateReference(value: string): string {
  if (!CERTIFICATE_PATTERN.test(value)) throw new Error("The certificate reference is not valid for timestamp checkout.");
  return value;
}

function validateManifestSha256(value: string): string {
  if (!SHA256_PATTERN.test(value)) throw new Error("The manifest SHA-256 must be 64 lowercase hexadecimal characters.");
  return value;
}

export function validateStatusToken(value: string): string {
  if (!TOKEN_PATTERN.test(value)) throw new Error("The recovery code is not a valid status token.");
  return value;
}

function validateCheckoutUrl(value: unknown, mode: TimestampServiceConfig["mode"]): string {
  if (typeof value !== "string" || value.length > 2048 || value.trim() !== value
    || /[\\\u0000-\u0020\u007f]/.test(value) || hasUnsafeRawPath(value)) {
    throw new Error("The checkout service returned an invalid URL.");
  }
  const authority = /^https:\/\/([^/?#]+)/.exec(value)?.[1];
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("The checkout service returned an invalid URL.");
  }
  const pathMatch = /^\/c\/pay\/(cs_(test|live)_[A-Za-z0-9]{1,500})$/.exec(url.pathname);
  const expectedSessionMode = mode === "production" ? "live" : "test";
  const hasFragmentMarker = value.includes("#");
  const fragment = url.hash.slice(1);
  // Stripe can return the canonical session path without optional browser-state data.
  const fragmentValid = !hasFragmentMarker || CHECKOUT_FRAGMENT_PATTERN.test(fragment);
  if (authority !== "checkout.stripe.com" || url.protocol !== "https:" || url.host !== "checkout.stripe.com"
    || url.username || url.password || url.search || !pathMatch || !CHECKOUT_SESSION_PATTERN.test(pathMatch[1])
    || pathMatch[2] !== expectedSessionMode || !fragmentValid) {
    throw new Error("Checkout was blocked because the destination is not Stripe's exact secure host.");
  }
  return value;
}

async function readBounded(response: Response, maximumBytes: number): Promise<Uint8Array> {
  const declaredLength = response.headers.get("content-length");
  if (declaredLength !== null && (!/^\d+$/.test(declaredLength) || Number(declaredLength) > maximumBytes)) {
    throw new Error("The timestamp service response is too large.");
  }
  const reader = response.body?.getReader();
  if (!reader) {
    const buffer = new Uint8Array(await response.arrayBuffer());
    if (buffer.byteLength > maximumBytes) throw new Error("The timestamp service response is too large.");
    return buffer;
  }
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maximumBytes) {
      await reader.cancel();
      throw new Error("The timestamp service response is too large.");
    }
    chunks.push(value);
  }
  const output = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

function requireNoStore(response: Response): void {
  const cacheControl = response.headers.get("cache-control")?.toLowerCase() ?? "";
  if (!cacheControl.split(",").some((part) => part.trim() === "no-store")) {
    throw new Error("The timestamp service did not mark this private response no-store.");
  }
}

async function readJson(response: Response, containsToken = false): Promise<unknown> {
  if (containsToken) requireNoStore(response);
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") throw new Error("The timestamp service returned an unsafe content type.");
  const bytes = await readBounded(response, MAX_JSON_BYTES);
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new Error("The timestamp service returned invalid JSON.");
  }
}

export class TimestampServiceError extends Error {
  constructor(message: string, readonly code: "auth" | "deterministic" | "remote") {
    super(message);
    this.name = "TimestampServiceError";
  }
}

export function isTimestampAuthError(error: unknown): boolean {
  return error instanceof TimestampServiceError && error.code === "auth";
}

export function isDeterministicCheckoutError(error: unknown): boolean {
  return error instanceof TimestampServiceError && error.code === "deterministic";
}

async function requireOk(response: Response, checkout = false): Promise<void> {
  if (response.ok) return;
  if (!checkout && (response.status === 401 || response.status === 403)) throw new TimestampServiceError("This recovery code is invalid, expired, or revoked.", "auth");
  if (!checkout && response.status === 404) throw new TimestampServiceError("No timestamp order was found for this recovery code.", "auth");
  const code = checkout && response.status >= 400 && response.status < 500 && ![408, 425, 429].includes(response.status)
    ? "deterministic"
    : "remote";
  if (response.status === 409) throw new TimestampServiceError("The timestamp order changed; refresh its status before trying again.", code);
  if (response.status === 429) throw new TimestampServiceError("Too many requests. Wait before checking again.", code);
  throw new TimestampServiceError(`The timestamp service request failed (${response.status}).`, code);
}

function parseCheckoutResponse(value: unknown, mode: TimestampServiceConfig["mode"]): CheckoutResponse {
  if (!isRecord(value) || !hasExactKeys(value, ["order_reference", "status_token", "checkout_url", "payment_state", "fulfillment_state"])) {
    throw new Error("The timestamp service returned an unexpected checkout response.");
  }
  if (typeof value.order_reference !== "string" || !ORDER_PATTERN.test(value.order_reference)
    || typeof value.status_token !== "string" || !TOKEN_PATTERN.test(value.status_token)
    || value.payment_state !== "checkout_open" || value.fulfillment_state !== "awaiting_payment") {
    throw new Error("The timestamp service returned an invalid checkout response.");
  }
  return {
    orderReference: value.order_reference,
    statusToken: value.status_token,
    checkoutUrl: validateCheckoutUrl(value.checkout_url, mode),
    paymentState: value.payment_state,
    fulfillmentState: value.fulfillment_state,
  };
}

const paymentStates = new Set<PaymentState>(["checkout_open", "processing", "paid", "failed", "expired", "refunded", "disputed"]);
const fulfillmentStates = new Set<FulfillmentState>(["awaiting_payment", "queued", "stamping", "calendar_pending", "bitcoin_verified", "delivered", "manual_review"]);

function parseOrderStatus(value: unknown): OrderStatus {
  const required = ["order_reference", "certificate_reference", "manifest_sha256", "payment_state", "fulfillment_state", "created_at", "updated_at", "proof_available"];
  const optional = ["calendar_submitted_at", "bitcoin_verified_at", "message_code"];
  if (!isRecord(value) || !hasExactKeys(value, required, optional)) throw new Error("The timestamp service returned an unexpected order status.");
  if (typeof value.order_reference !== "string" || !ORDER_PATTERN.test(value.order_reference)
    || typeof value.certificate_reference !== "string" || !CERTIFICATE_PATTERN.test(value.certificate_reference)
    || typeof value.manifest_sha256 !== "string" || !SHA256_PATTERN.test(value.manifest_sha256)
    || typeof value.payment_state !== "string" || !paymentStates.has(value.payment_state as PaymentState)
    || typeof value.fulfillment_state !== "string" || !fulfillmentStates.has(value.fulfillment_state as FulfillmentState)
    || !isDateTime(value.created_at) || !isDateTime(value.updated_at)
    || typeof value.proof_available !== "boolean"
    || (value.calendar_submitted_at !== undefined && !isDateTime(value.calendar_submitted_at))
    || (value.bitcoin_verified_at !== undefined && !isDateTime(value.bitcoin_verified_at))
    || (value.message_code !== undefined && (typeof value.message_code !== "string" || !MESSAGE_PATTERN.test(value.message_code)))) {
    throw new Error("The timestamp service returned an invalid order status.");
  }
  const paymentState = value.payment_state as PaymentState;
  const fulfillmentState = value.fulfillment_state as FulfillmentState;
  const hasCalendarTime = value.calendar_submitted_at !== undefined;
  const hasBitcoinTime = value.bitcoin_verified_at !== undefined;
  const proofAvailable = value.proof_available;
  const proofRequiredState = fulfillmentState === "calendar_pending" || fulfillmentState === "delivered";
  const proofForbiddenState = fulfillmentState === "awaiting_payment"
    || fulfillmentState === "queued"
    || fulfillmentState === "stamping"
    || fulfillmentState === "manual_review";
  const prePayment = new Set<PaymentState>(["checkout_open", "processing", "failed", "expired"]);
  if (prePayment.has(paymentState) && fulfillmentState !== "awaiting_payment" && fulfillmentState !== "manual_review") {
    throw new Error("The timestamp service returned a contradictory payment and fulfillment status.");
  }
  if (paymentState === "paid" && fulfillmentState === "awaiting_payment") {
    throw new Error("The timestamp service returned a contradictory paid status.");
  }
  if (["awaiting_payment", "queued", "stamping", "manual_review"].includes(fulfillmentState) && (hasCalendarTime || hasBitcoinTime)) {
    throw new Error("The timestamp service returned timestamps for an unfinished fulfillment state.");
  }
  if (proofRequiredState && !proofAvailable) {
    throw new Error("The timestamp service returned a proof-bearing state without an available proof.");
  }
  if (proofForbiddenState && proofAvailable) {
    throw new Error("The timestamp service returned an available proof for a state without a downloadable bundle.");
  }
  if (fulfillmentState !== "bitcoin_verified" && fulfillmentState !== "delivered" && hasBitcoinTime) {
    throw new Error("The timestamp service returned Bitcoin verification time outside a verified state.");
  }
  if (fulfillmentState === "calendar_pending" && (!hasCalendarTime || hasBitcoinTime)) {
    throw new Error("The timestamp service returned an invalid pending-calendar status.");
  }
  if ((fulfillmentState === "bitcoin_verified" || fulfillmentState === "delivered") && (!hasCalendarTime || !hasBitcoinTime)) {
    throw new Error("The timestamp service returned an incomplete Bitcoin verification status.");
  }
  if (hasBitcoinTime && !hasCalendarTime) throw new Error("The timestamp service returned Bitcoin time without calendar submission time.");
  if (hasCalendarTime && hasBitcoinTime && Date.parse(value.bitcoin_verified_at as string) < Date.parse(value.calendar_submitted_at as string)) {
    throw new Error("The timestamp service returned timestamps in an invalid order.");
  }
  return {
    orderReference: value.order_reference,
    certificateReference: value.certificate_reference,
    manifestSha256: value.manifest_sha256,
    paymentState,
    fulfillmentState,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
    calendarSubmittedAt: value.calendar_submitted_at as string | undefined,
    bitcoinVerifiedAt: value.bitcoin_verified_at as string | undefined,
    proofAvailable,
    messageCode: value.message_code as string | undefined,
  };
}

function bearerRequest(token: string, signal?: AbortSignal): RequestInit {
  return {
    headers: { Authorization: `Bearer ${validateStatusToken(token)}`, "Cache-Control": "no-store" },
    cache: "no-store",
    credentials: "omit",
    referrerPolicy: "no-referrer",
    signal,
  };
}

export function createTimestampService(config: TimestampServiceConfig, fetcher: typeof fetch = fetch) {
  return {
    config,
    async createCheckout(attempt: CheckoutAttempt, signal?: AbortSignal): Promise<CheckoutResponse> {
      validateCheckoutAttempt(attempt);
      const response = await fetcher(endpoint(config, "v1/checkout"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": attempt.idempotencyKey,
          "Cache-Control": "no-store",
        },
        body: attempt.body,
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal,
      });
      await requireOk(response, true);
      return parseCheckoutResponse(await readJson(response, true), config.mode);
    },
    async getStatus(token: string, signal?: AbortSignal): Promise<OrderStatus> {
      const response = await fetcher(endpoint(config, "v1/orders/status"), bearerRequest(token, signal));
      await requireOk(response);
      return parseOrderStatus(await readJson(response, true));
    },
    async rotateToken(token: string, signal?: AbortSignal): Promise<string> {
      const response = await fetcher(endpoint(config, "v1/orders/rotate-token"), { ...bearerRequest(token, signal), method: "POST" });
      await requireOk(response);
      const value = await readJson(response, true);
      if (!isRecord(value) || !hasExactKeys(value, ["status_token"]) || typeof value.status_token !== "string") {
        throw new Error("The timestamp service returned an invalid replacement recovery code.");
      }
      return validateStatusToken(value.status_token);
    },
    async downloadProof(token: string, certificateReference: string, signal?: AbortSignal): Promise<ProofDownload> {
      const response = await fetcher(endpoint(config, "v1/orders/proof"), bearerRequest(token, signal));
      await requireOk(response);
      requireNoStore(response);
      const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
      if (contentType !== "application/zip" && contentType !== "application/octet-stream") {
        throw new Error("The timestamp proof has an unsafe content type.");
      }
      const disposition = response.headers.get("content-disposition") ?? "";
      if (!/^attachment(?:;|$)/i.test(disposition) || /[\r\n]/.test(disposition)) {
        throw new Error("The timestamp proof has an unsafe download disposition.");
      }
      if (/;\s*filename\*/i.test(disposition)) throw new Error("The timestamp proof has an unsafe server filename.");
      const remoteName = /;\s*filename\s*=\s*(?:"([^"]*)"|([^;\s]*))/i.exec(disposition);
      const suppliedName = remoteName?.[1] ?? remoteName?.[2];
      if (suppliedName !== undefined && (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,179}\.zip$/i.test(suppliedName)
        || TOKEN_PATTERN.test(suppliedName.replace(/\.zip$/i, "")))) {
        throw new Error("The timestamp proof has an unsafe server filename.");
      }
      const bytes = await readBounded(response, MAX_PROOF_BYTES);
      if (bytes.byteLength === 0) throw new Error("The timestamp proof download was empty.");
      const safeReference = validateCertificateReference(certificateReference).replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "certificate";
      const blobBytes = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
      return {
        blob: new Blob([blobBytes], { type: contentType }),
        fileName: `${safeReference}-bitcoin-timestamp.zip`,
      };
    },
  };
}

function checkoutBody(request: CheckoutRequest, acceptedAt: string): {
  certificate_reference: string;
  manifest_sha256: string;
  email: string;
  consent: { managed_timestamp: true; terms_version: string; privacy_version: string; accepted_at: string };
} {
  if (!isDateTime(acceptedAt)) throw new Error("The consent time is invalid.");
  const policyVersion = validatePolicyVersion(request.policyVersion);
  if (!policyVersion) throw new Error("The consent policy version is invalid.");
  return {
    certificate_reference: validateCertificateReference(request.certificateReference),
    manifest_sha256: validateManifestSha256(request.manifestSha256),
    email: validateTimestampEmail(request.email),
    consent: {
      managed_timestamp: true,
      terms_version: policyVersion,
      privacy_version: policyVersion,
      accepted_at: acceptedAt,
    },
  };
}

export function createCheckoutAttempt(request: CheckoutRequest): CheckoutAttempt {
  const acceptedAt = request.acceptedAt ?? new Date().toISOString();
  const body = checkoutBody(request, acceptedAt);
  return {
    idempotencyKey: createIdempotencyKey(),
    body: JSON.stringify(body),
    certificateReference: body.certificate_reference,
    manifestSha256: body.manifest_sha256,
    email: body.email,
    policyVersion: body.consent.terms_version,
    acceptedAt,
  };
}

function validateCheckoutAttempt(attempt: CheckoutAttempt): void {
  if (!IDEMPOTENCY_PATTERN.test(attempt.idempotencyKey)) throw new Error("The checkout idempotency key is invalid.");
  const expected = checkoutBody({
    certificateReference: attempt.certificateReference,
    manifestSha256: attempt.manifestSha256,
    email: attempt.email,
    policyVersion: attempt.policyVersion,
  }, attempt.acceptedAt);
  if (attempt.body !== JSON.stringify(expected)) throw new Error("The checkout attempt binding is invalid.");
}

export function checkoutAttemptMatches(attempt: CheckoutAttempt, request: CheckoutRequest): boolean {
  try {
    return attempt.body === JSON.stringify(checkoutBody(request, attempt.acceptedAt));
  } catch {
    return false;
  }
}

export type TimestampService = ReturnType<typeof createTimestampService>;

export async function pollOrderStatus(
  service: TimestampService,
  token: string,
  options: {
    signal?: AbortSignal;
    maxAttempts?: number;
    onUpdate?: (status: OrderStatus) => void;
    stopWhen?: (status: OrderStatus) => boolean;
    waitUntilVisible?: () => Promise<void>;
  } = {},
): Promise<OrderStatus> {
  const maxAttempts = Math.min(Math.max(options.maxAttempts ?? 6, 1), 10);
  let latest: OrderStatus | undefined;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (options.signal?.aborted) throw new DOMException("Polling was aborted.", "AbortError");
    if (attempt > 0) {
      if (options.waitUntilVisible) await options.waitUntilVisible();
      const delay = Math.min(1_500 * 2 ** (attempt - 1), 30_000) + Math.floor(Math.random() * 500);
      await new Promise<void>((resolve, reject) => {
        const timer = globalThis.setTimeout(resolve, delay);
        options.signal?.addEventListener("abort", () => {
          globalThis.clearTimeout(timer);
          reject(new DOMException("Polling was aborted.", "AbortError"));
        }, { once: true });
      });
    }
    latest = await service.getStatus(token, options.signal);
    options.onUpdate?.(latest);
    if (options.stopWhen?.(latest)) return latest;
  }
  if (!latest) throw new Error("No timestamp status was received.");
  return latest;
}

function parseTimestampSession(value: unknown, config: TimestampServiceConfig): TimestampSession | undefined {
  const required = ["token", "orderRef", "certificateReference", "manifestSha256", "apiBase", "apiVersion"];
  if (!isRecord(value) || !hasExactKeys(value, required, ["checkoutUrl"])
    || typeof value.token !== "string" || !TOKEN_PATTERN.test(value.token)
    || typeof value.orderRef !== "string" || !ORDER_PATTERN.test(value.orderRef)
    || typeof value.certificateReference !== "string" || !CERTIFICATE_PATTERN.test(value.certificateReference)
    || typeof value.manifestSha256 !== "string" || !SHA256_PATTERN.test(value.manifestSha256)
    || value.apiBase !== config.baseUrl || value.apiVersion !== config.contractVersion
    || (value.checkoutUrl !== undefined && (() => {
      try {
        return validateCheckoutUrl(value.checkoutUrl, config.mode) !== value.checkoutUrl;
      } catch {
        return true;
      }
    })())) return undefined;
  return value as unknown as TimestampSession;
}

export function loadTimestampSession(config: TimestampServiceConfig, storage: Storage = sessionStorage): TimestampSession | undefined {
  const raw = storage.getItem(TIMESTAMP_SESSION_KEY);
  if (!raw) return undefined;
  if (raw.length > 4096) {
    storage.removeItem(TIMESTAMP_SESSION_KEY);
    return undefined;
  }
  try {
    const session = parseTimestampSession(JSON.parse(raw), config);
    if (!session) storage.removeItem(TIMESTAMP_SESSION_KEY);
    return session;
  } catch {
    storage.removeItem(TIMESTAMP_SESSION_KEY);
    return undefined;
  }
}

export function saveTimestampSession(session: TimestampSession, config: TimestampServiceConfig, storage: Storage = sessionStorage): void {
  const validated = parseTimestampSession(session, config);
  if (!validated) throw new Error("The timestamp recovery session is invalid.");
  storage.setItem(TIMESTAMP_SESSION_KEY, JSON.stringify(validated));
}

export function forgetTimestampSession(storage: Storage = sessionStorage): void {
  storage.removeItem(TIMESTAMP_SESSION_KEY);
}
