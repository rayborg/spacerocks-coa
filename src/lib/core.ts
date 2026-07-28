const encoder = new TextEncoder();

export function utf8(value: string): Uint8Array<ArrayBuffer> {
  return encoder.encode(value);
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Bytes(
  data: ArrayBuffer | Uint8Array<ArrayBufferLike>,
): Promise<Uint8Array<ArrayBuffer>> {
  const owned = data instanceof ArrayBuffer ? data : new Uint8Array(data).buffer;
  return new Uint8Array(await crypto.subtle.digest("SHA-256", owned));
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array<ArrayBufferLike>): Promise<string> {
  return bytesToHex(await sha256Bytes(data));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }

  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((sorted, key) => {
        const next = (value as Record<string, unknown>)[key];
        if (next !== undefined) {
          sorted[key] = sortJson(next);
        }
        return sorted;
      }, {});
  }

  return value;
}

export function stableStringify(value: unknown): string {
  return `${JSON.stringify(sortJson(value), null, 2)}\n`;
}

export function formatFingerprint(hex: string): string {
  return hex
    .toUpperCase()
    .match(/.{1,2}/g)
    ?.join(":") ?? "";
}

export function sanitizeFileName(name: string): string {
  const basename = name.replaceAll("\\", "/").split("/").pop() ?? "file";
  const normalized = basename.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const safe = normalized.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return safe && safe !== "." && safe !== ".." ? safe : "file";
}

export function uniqueFileName(name: string, used: Set<string>): string {
  const safe = sanitizeFileName(name);
  const dot = safe.lastIndexOf(".");
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const extension = dot > 0 ? safe.slice(dot) : "";
  let candidate = safe;
  let suffix = 2;

  while (used.has(candidate.toLowerCase())) {
    candidate = `${stem}-${suffix}${extension}`;
    suffix += 1;
  }

  used.add(candidate.toLowerCase());
  return candidate;
}

export function mediaTypeForFile(file: File): string {
  return file.type || "application/octet-stream";
}

export function displayDate(date: string): string {
  if (!date) return "Not recorded";
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return date;
  return parsed.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
