import { formatFingerprint, sha256Hex, utf8 } from "./core";
import type { SigningIdentity } from "../types";

const KEY_BUNDLE_FORMAT = "SPACEROCKS-ENCRYPTED-ED25519-KEY";
const KEY_BUNDLE_VERSION = 1;
const PBKDF2_ITERATIONS = 600_000;
const AUTHENTICATED_CONTEXT = utf8("SPACEROCKS-COA-KEY-V1");

interface EncryptedKeyBundle {
  format: typeof KEY_BUNDLE_FORMAT;
  version: typeof KEY_BUNDLE_VERSION;
  createdAt: string;
  publicKeyAlgorithm: "Ed25519";
  publicKeyFingerprint: string;
  publicKeyPem: string;
  encryptedPrivateKey: string;
  kdf: {
    name: "PBKDF2";
    hash: "SHA-256";
    iterations: number;
    salt: string;
  };
  cipher: {
    name: "AES-GCM";
    iv: string;
    tagLength: 128;
  };
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value.replace(/\s/g, ""));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function derToPem(der: ArrayBuffer, label: string): string {
  const base64 = bytesToBase64(new Uint8Array(der));
  const lines = base64.match(/.{1,64}/g)?.join("\n") ?? base64;
  return `-----BEGIN ${label}-----\n${lines}\n-----END ${label}-----\n`;
}

export function pemToDer(pem: string): Uint8Array<ArrayBuffer> {
  const base64 = pem.replace(/-----BEGIN [^-]+-----|-----END [^-]+-----|\s/g, "");
  return base64ToBytes(base64);
}

async function deriveEncryptionKey(passphrase: string, salt: Uint8Array<ArrayBufferLike>, iterations: number) {
  const ownedSalt = new Uint8Array(salt);
  const baseKey = await crypto.subtle.importKey("raw", utf8(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt: ownedSalt, iterations },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function fingerprintForSpki(spki: ArrayBuffer): Promise<string> {
  return formatFingerprint(await sha256Hex(spki));
}

export async function generateSigningIdentity(passphrase: string): Promise<{
  identity: SigningIdentity;
  encryptedBundle: string;
}> {
  const pair = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const spki = await crypto.subtle.exportKey("spki", pair.publicKey);
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const publicKeyPem = derToPem(spki, "PUBLIC KEY");
  const fingerprint = await fingerprintForSpki(spki);

  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encryptionKey = await deriveEncryptionKey(passphrase, salt, PBKDF2_ITERATIONS);
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: AUTHENTICATED_CONTEXT, tagLength: 128 },
    encryptionKey,
    pkcs8,
  );

  const bundle: EncryptedKeyBundle = {
    format: KEY_BUNDLE_FORMAT,
    version: KEY_BUNDLE_VERSION,
    createdAt: new Date().toISOString(),
    publicKeyAlgorithm: "Ed25519",
    publicKeyFingerprint: fingerprint,
    publicKeyPem,
    encryptedPrivateKey: bytesToBase64(new Uint8Array(encrypted)),
    kdf: {
      name: "PBKDF2",
      hash: "SHA-256",
      iterations: PBKDF2_ITERATIONS,
      salt: bytesToBase64(salt),
    },
    cipher: {
      name: "AES-GCM",
      iv: bytesToBase64(iv),
      tagLength: 128,
    },
  };

  return {
    identity: {
      privateKey: pair.privateKey,
      publicKey: pair.publicKey,
      publicKeyPem,
      fingerprint,
      source: "generated",
    },
    encryptedBundle: `${JSON.stringify(bundle, null, 2)}\n`,
  };
}

export async function importSigningIdentity(bundleText: string, passphrase: string): Promise<SigningIdentity> {
  let bundle: EncryptedKeyBundle;
  try {
    bundle = JSON.parse(bundleText) as EncryptedKeyBundle;
  } catch {
    throw new Error("The selected key backup is not valid JSON.");
  }

  if (
    bundle.format !== KEY_BUNDLE_FORMAT ||
    bundle.version !== KEY_BUNDLE_VERSION ||
    bundle.publicKeyAlgorithm !== "Ed25519" ||
    bundle.kdf?.name !== "PBKDF2" ||
    bundle.cipher?.name !== "AES-GCM"
  ) {
    throw new Error("This is not a supported Spacerocks encrypted Ed25519 key backup.");
  }

  try {
    const salt = base64ToBytes(bundle.kdf.salt);
    const iv = base64ToBytes(bundle.cipher.iv);
    const key = await deriveEncryptionKey(passphrase, salt, bundle.kdf.iterations);
    const pkcs8 = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv,
        additionalData: AUTHENTICATED_CONTEXT,
        tagLength: bundle.cipher.tagLength,
      },
      key,
      base64ToBytes(bundle.encryptedPrivateKey),
    );
    const privateKey = await crypto.subtle.importKey("pkcs8", pkcs8, "Ed25519", false, ["sign"]);
    const publicKeyDer = pemToDer(bundle.publicKeyPem);
    const publicKey = await crypto.subtle.importKey("spki", publicKeyDer, "Ed25519", true, ["verify"]);
    const fingerprint = await fingerprintForSpki(publicKeyDer.buffer as ArrayBuffer);

    if (fingerprint !== bundle.publicKeyFingerprint) {
      throw new Error("The public-key fingerprint in the backup does not match its public key.");
    }

    const challenge = crypto.getRandomValues(new Uint8Array(32));
    const signature = await crypto.subtle.sign("Ed25519", privateKey, challenge);
    const validPair = await crypto.subtle.verify("Ed25519", publicKey, signature, challenge);
    if (!validPair) {
      throw new Error("The encrypted private key does not match the bundled public key.");
    }

    return {
      privateKey,
      publicKey,
      publicKeyPem: bundle.publicKeyPem,
      fingerprint,
      source: "imported",
    };
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("The ")) throw error;
    throw new Error("The key could not be decrypted. Check the backup file and passphrase.");
  }
}

export async function signBytes(
  privateKey: CryptoKey,
  bytes: ArrayBuffer | Uint8Array<ArrayBufferLike>,
): Promise<Uint8Array<ArrayBuffer>> {
  const owned = bytes instanceof ArrayBuffer ? bytes : new Uint8Array(bytes).buffer;
  return new Uint8Array(await crypto.subtle.sign("Ed25519", privateKey, owned));
}

export async function publicKeyFingerprint(publicKeyPem: string): Promise<string> {
  return fingerprintForSpki(pemToDer(publicKeyPem).buffer as ArrayBuffer);
}
