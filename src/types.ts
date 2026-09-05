import type { CertificateThemeId } from "./certificateThemes";
import type { CertificateStyleId } from "./certificateStyles";

export type CertificateStatus = "active" | "superseded" | "revoked" | "transferred";

export interface FormValues {
  issuerName: string;
  collectionName: string;
  issuerEmail: string;
  issuerPhone: string;
  issuerAddress: string;
  issuerWebsite: string;
  certificateId: string;
  issueDate: string;
  certificateVersion: string;
  certificateStatus: CertificateStatus;
  certificateStyle: CertificateStyleId;
  certificateTheme: CertificateThemeId;
  supersededCertificateId: string;
  certificateNotes: string;
  meteoriteIdentity: "official" | "unclassified";
  meteoriteName: string;
  meteoriteType: string;
  classification: string;
  meteoriteSubclass: string;
  suspectedType: string;
  officialNameVerified: boolean;
  officialClassificationExceptionAttested?: boolean;
  weightGrams: string;
  weightPrecision: string;
  specimenForm: string;
  dimensions: string;
  numberOfPieces: string;
  preparationState: string;
  identifyingMarks: string;
  recordedOwner: string;
  fallStatus: string;
  fallDate: string;
  country: string;
  region: string;
  locality: string;
  latitude: string;
  longitude: string;
  metbullCode: string;
  officialReferenceUrl: string;
  finderName: string;
  recoveryInformation: string;
  provenance: string;
  previousOwner: string;
  intermediaryPurchaserName: string;
  buyer: string;
  transferDate: string;
  invoiceReference: string;
  transferNotes: string;
}

export interface PhotoInput {
  id: string;
  file: File;
  previewUrl: string;
  caption: string;
  captureDate: string;
  isUnmodifiedOriginal: boolean;
  pixelWidth: number;
  pixelHeight: number;
}

export interface DisplayCrop {
  x: number;
  y: number;
  width: number;
  height: number;
  targetAspect: "112:91";
  algorithm: "center-cover-v1";
}

export interface SigningIdentity {
  privateKey: CryptoKey;
  publicKey: CryptoKey;
  publicKeyPem: string;
  fingerprint: string;
  source: "generated" | "imported";
}

export interface ManifestFile {
  path: string;
  role: string;
  mediaType: string;
  bytes: number;
  sha256: string;
}

export interface VerificationCheck {
  label: string;
  status: "pass" | "fail" | "warning";
  detail: string;
}

export interface VerificationResult {
  valid: boolean;
  certificateId: string;
  fingerprint: string;
  checks: VerificationCheck[];
}
