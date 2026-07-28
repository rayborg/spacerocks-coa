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
  supersededCertificateId: string;
  certificateNotes: string;
  meteoriteName: string;
  classification: string;
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
  recoveryInformation: string;
  provenance: string;
  previousOwner: string;
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
