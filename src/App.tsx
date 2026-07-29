import { startTransition, useDeferredValue, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import type {
  FormValues,
  PhotoInput,
  SigningIdentity,
  VerificationResult,
} from "./types";
import { downloadBlob, sanitizeFileName } from "./lib/core";
import { generateSigningIdentity, importSigningIdentity } from "./lib/crypto";
import {
  certificateThemeIds,
  certificateThemes,
  getCertificateTheme,
} from "./certificateThemes";

const requiredText = (label: string) => z.string().trim().min(1, `${label} is required.`);
const optionalEmail = z
  .string()
  .trim()
  .refine((value) => !value || z.string().email().safeParse(value).success, "Enter a valid email address.");
const optionalUrl = z
  .string()
  .trim()
  .refine((value) => !value || z.string().url().safeParse(value).success, "Enter a complete URL, including https://.");
const positiveNumber = z
  .string()
  .trim()
  .refine((value) => Number.isFinite(Number(value)) && Number(value) > 0, "Enter a number greater than zero.");
const nonNegativeNumber = z
  .string()
  .trim()
  .refine((value) => Number.isFinite(Number(value)) && Number(value) >= 0, "Enter zero or a positive number.");
const positiveInteger = z
  .string()
  .trim()
  .refine((value) => /^\d+$/.test(value) && Number(value) >= 1, "Enter a whole number of at least one.");

const formSchema = z
  .object({
    issuerName: requiredText("Issuer name"),
    collectionName: requiredText("Collection or business name"),
    issuerEmail: optionalEmail,
    issuerPhone: z.string(),
    issuerAddress: z.string(),
    issuerWebsite: optionalUrl,
    certificateId: z
      .string()
      .trim()
      .regex(/^[A-Za-z0-9][A-Za-z0-9._-]{1,119}$/, "Use 2-120 letters, numbers, periods, underscores, or hyphens."),
    issueDate: requiredText("Issue date"),
    certificateVersion: requiredText("Certificate version"),
    certificateStatus: z.enum(["active", "superseded", "revoked", "transferred"]),
    certificateTheme: z.enum(certificateThemeIds),
    supersededCertificateId: z.string(),
    certificateNotes: z.string(),
    meteoriteName: requiredText("Meteorite name"),
    classification: requiredText("Classification"),
    weightGrams: positiveNumber,
    weightPrecision: nonNegativeNumber,
    specimenForm: requiredText("Specimen form"),
    dimensions: z.string(),
    numberOfPieces: positiveInteger,
    preparationState: z.string(),
    identifyingMarks: z.string(),
    recordedOwner: requiredText("Recorded owner"),
    fallStatus: requiredText("Fall or find status"),
    fallDate: requiredText("Fall or find date"),
    country: requiredText("Country"),
    region: z.string(),
    locality: requiredText("Locality"),
    latitude: requiredText("Latitude"),
    longitude: requiredText("Longitude"),
    metbullCode: z.string(),
    officialReferenceUrl: optionalUrl,
    recoveryInformation: z.string(),
    provenance: z.string().trim().min(10, "Provide a meaningful provenance statement."),
    previousOwner: z.string(),
    buyer: z.string(),
    transferDate: z.string(),
    invoiceReference: z.string(),
    transferNotes: z.string(),
  })
  .superRefine((values, context) => {
    if (values.certificateStatus === "superseded" && !values.supersededCertificateId.trim()) {
      context.addIssue({
        code: "custom",
        path: ["supersededCertificateId"],
        message: "Record the certificate ID this version supersedes.",
      });
    }
  });

const defaultValues: FormValues = {
  issuerName: "Raymond Borges Hink",
  collectionName: "The Spacerocks Collection",
  issuerEmail: "",
  issuerPhone: "",
  issuerAddress: "",
  issuerWebsite: "",
  certificateId: "AZ-2019-0447-HE",
  issueDate: "2026-07-25",
  certificateVersion: "1.0",
  certificateStatus: "active",
  certificateTheme: "observatory-navy",
  supersededCertificateId: "",
  certificateNotes: "",
  meteoriteName: "Aguas Zarcas",
  classification: "CM2 carbonaceous chondrite",
  weightGrams: "44.7",
  weightPrecision: "0.1",
  specimenForm: "Half stone / end cut",
  dimensions: "",
  numberOfPieces: "1",
  preparationState: "Half stone with exposed cut face",
  identifyingMarks: "",
  recordedOwner: "Raymond Borges Hink",
  fallStatus: "Witnessed fall",
  fallDate: "2019-04-23",
  country: "Costa Rica",
  region: "Alajuela Province",
  locality: "Alajuela Province, Costa Rica",
  latitude: "10\u00b023\u203229.03\u2033 N",
  longitude: "84\u00b020\u203228.58\u2033 W",
  metbullCode: "69696",
  officialReferenceUrl: "https://www.lpi.usra.edu/meteor/metbull.cfm?code=69696",
  recoveryInformation: "",
  provenance: "Recorded in The Spacerocks Collection as the exact 44.7 g half stone / end cut represented by this certificate package.",
  previousOwner: "",
  buyer: "",
  transferDate: "",
  invoiceReference: "",
  transferNotes: "",
};

function Field({
  label,
  hint,
  error,
  wide = false,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className={`field${wide ? " field--wide" : ""}`}>
      <span className="field__label">{label}</span>
      {children}
      {hint && !error ? <span className="field__hint">{hint}</span> : null}
      {error ? <span className="field__error">{error}</span> : null}
    </label>
  );
}

function CertificatePreview({
  values,
  photo,
  identity,
}: {
  values: FormValues;
  photo?: PhotoInput;
  identity?: SigningIdentity;
}) {
  const statusClass = values.certificateStatus === "active" ? "" : " certificate-preview--flagged";
  const theme = getCertificateTheme(values.certificateTheme);
  const themeStyle = {
    "--certificate-dark": theme.dark,
    "--certificate-dark-soft": theme.darkSoft,
    "--certificate-accent": theme.accent,
    "--certificate-accent-light": theme.accentLight,
    "--certificate-paper": theme.paper,
    "--certificate-ink": theme.ink,
    "--certificate-muted": theme.muted,
    "--certificate-accent-text": theme.accentText,
  } as React.CSSProperties;
  return (
    <div
      className={`certificate-preview${statusClass}`}
      style={themeStyle}
      data-certificate-theme={theme.id}
      aria-label={`Live certificate preview in ${theme.name}`}
    >
      <div className="certificate-preview__frame">
        <header className="certificate-preview__header">
          <div className="certificate-preview__collection">
            <span className="orbit-mark" aria-hidden="true"><i /></span>
            <span>{values.collectionName || "Collection name"}</span>
          </div>
          <strong>Certificate of Authenticity</strong>
          <div className="certificate-preview__id">
            <span>Certificate ID</span>
            {values.certificateId || "Pending"}
          </div>
        </header>
        <div className="certificate-preview__body">
          <div className="certificate-preview__title">
            <h3>{values.meteoriteName || "Meteorite name"}</h3>
            <p>{values.classification || "Classification"}</p>
          </div>
          <div className="certificate-preview__photo">
            {photo ? <img src={photo.previewUrl} alt={photo.caption || "Uploaded specimen"} /> : <span>Exact specimen photo required</span>}
          </div>
          <dl className="certificate-preview__facts">
            <div><dt>Fall / find</dt><dd>{values.fallStatus}</dd></div>
            <div><dt>Locality</dt><dd>{values.locality}</dd></div>
            <div><dt>Specimen form</dt><dd>{values.specimenForm}</dd></div>
            <div><dt>Recorded owner</dt><dd>{values.recordedOwner}</dd></div>
          </dl>
          <div className="certificate-preview__weight">
            <span>Recorded weight</span>
            <strong>{values.weightGrams || "0"}<small> g</small></strong>
            <em>{values.specimenForm}</em>
          </div>
          <div className="certificate-preview__signoff">
            <span>Digitally signed by</span>
            <strong>{values.issuerName || "Issuer"}</strong>
            <small>{identity ? `Key ${identity.fingerprint.slice(0, 17)}...` : "Signing key not loaded"}</small>
          </div>
          <div className="certificate-preview__seal" aria-hidden="true">
            <span>SHA</span>
            <i />
            <small>256</small>
          </div>
          {values.certificateStatus !== "active" ? (
            <div className="certificate-preview__status">{values.certificateStatus}</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function PackageVerifier() {
  const [result, setResult] = useState<VerificationResult>();
  const [fileName, setFileName] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const verify = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setStatus("Opening and hashing the package locally...");
    setFileName(file.name);
    setResult(undefined);
    try {
      const { verifyCertificateZip } = await import("./lib/verifier");
      const nextResult = await verifyCertificateZip(file);
      startTransition(() => setResult(nextResult));
      setStatus(nextResult.valid ? "All required cryptographic checks passed." : "One or more required checks failed.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "The package could not be verified.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="verifier">
      <div className="verifier__drop">
        <span className="verifier__icon" aria-hidden="true">V</span>
        <h3>Inspect a COA package</h3>
        <p>Select the original ZIP. It stays on this device while the manifest, signature, fingerprint, record, and file hashes are checked.</p>
        <label className="file-button">
          <input
            type="file"
            accept=".zip,application/zip"
            disabled={busy}
            onChange={(event) => void verify(event.target.files?.[0])}
          />
          {busy ? "Verifying..." : "Choose verification ZIP"}
        </label>
        {fileName ? <small>{fileName}</small> : null}
      </div>
      <div className="verifier__report" aria-live="polite">
        <div className="verifier__report-head">
          <span>Verification report</span>
          {result ? <strong className={result.valid ? "is-valid" : "is-invalid"}>{result.valid ? "PASS" : "FAIL"}</strong> : <strong>READY</strong>}
        </div>
        {result ? (
          <>
            <h3>{result.certificateId}</h3>
            <p className="fingerprint-text">{result.fingerprint}</p>
            <div className="check-list">
              {result.checks.map((check) => (
                <div className={`check check--${check.status}`} key={check.label}>
                  <i aria-hidden="true" />
                  <span><strong>{check.label}</strong><small>{check.detail}</small></span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="verifier__empty">
            <div className="hash-lines" aria-hidden="true"><i /><i /><i /><i /></div>
            <p>{status || "A signed result will appear here. No package contents are uploaded or retained."}</p>
          </div>
        )}
        {result ? <p className="verifier__status">{status}</p> : null}
      </div>
    </div>
  );
}

export default function App() {
  const {
    register,
    control,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues,
    mode: "onBlur",
  });
  const watchedValues = useWatch({ control });
  const previewValues = useDeferredValue({ ...defaultValues, ...watchedValues } as FormValues);

  const [photos, setPhotos] = useState<PhotoInput[]>([]);
  const [logo, setLogo] = useState<File>();
  const [identity, setIdentity] = useState<SigningIdentity>();
  const [encryptedBundle, setEncryptedBundle] = useState("");
  const [backupDownloaded, setBackupDownloaded] = useState(false);
  const [generatePassphrase, setGeneratePassphrase] = useState("");
  const [confirmPassphrase, setConfirmPassphrase] = useState("");
  const [importPassphrase, setImportPassphrase] = useState("");
  const [importFile, setImportFile] = useState<File>();
  const [keyStatus, setKeyStatus] = useState("No signing key is loaded.");
  const [keyBusy, setKeyBusy] = useState(false);
  const [photoStatus, setPhotoStatus] = useState("");
  const [generationStatus, setGenerationStatus] = useState("");
  const [generationBusy, setGenerationBusy] = useState(false);
  const [receipt, setReceipt] = useState<{ recordHash: string; manifestHash: string }>();

  const generateKey = async () => {
    if (generatePassphrase.length < 12) {
      setKeyStatus("Use a passphrase of at least 12 characters.");
      return;
    }
    if (generatePassphrase !== confirmPassphrase) {
      setKeyStatus("The two passphrases do not match.");
      return;
    }
    setKeyBusy(true);
    setKeyStatus("Generating and encrypting an Ed25519 key locally...");
    try {
      const generated = await generateSigningIdentity(generatePassphrase);
      setIdentity(generated.identity);
      setEncryptedBundle(generated.encryptedBundle);
      setBackupDownloaded(false);
      setKeyStatus("New signing identity created. Download the encrypted backup before issuing a certificate.");
      setGeneratePassphrase("");
      setConfirmPassphrase("");
    } catch (error) {
      setKeyStatus(error instanceof Error ? error.message : "This browser could not generate an Ed25519 key.");
    } finally {
      setKeyBusy(false);
    }
  };

  const downloadKeyBackup = () => {
    if (!encryptedBundle) return;
    const collection = sanitizeFileName(getValues("collectionName")) || "spacerocks";
    downloadBlob(new Blob([encryptedBundle], { type: "application/json" }), `${collection}-encrypted-ed25519-key-backup.json`);
    setBackupDownloaded(true);
    setKeyStatus("Encrypted key backup downloaded. Keep the file and its passphrase in separate secure locations.");
  };

  const importKey = async () => {
    if (!importFile || !importPassphrase) {
      setKeyStatus("Choose an encrypted key backup and enter its passphrase.");
      return;
    }
    setKeyBusy(true);
    setKeyStatus("Decrypting and validating the key pair locally...");
    try {
      const imported = await importSigningIdentity(await importFile.text(), importPassphrase);
      setIdentity(imported);
      setEncryptedBundle("");
      setBackupDownloaded(true);
      setImportPassphrase("");
      setKeyStatus("Existing signing identity loaded and matched to its public key.");
    } catch (error) {
      setKeyStatus(error instanceof Error ? error.message : "The signing identity could not be imported.");
    } finally {
      setKeyBusy(false);
    }
  };

  const forgetKey = () => {
    setIdentity(undefined);
    setEncryptedBundle("");
    setBackupDownloaded(false);
    setImportFile(undefined);
    setKeyStatus("The signing key was removed from this browser session.");
  };

  const addPhotos = (files: FileList | null) => {
    if (!files) return;
    const selected = Array.from(files);
    if (selected.some((file) => file.size > 100 * 1024 * 1024)) {
      setPhotoStatus("Each photograph must be 100 MB or smaller for browser packaging.");
      return;
    }
    if (photos.length + selected.length > 100) {
      setPhotoStatus("A package can contain at most 100 original photographs.");
      return;
    }
    const aggregateBytes = photos.reduce((total, photo) => total + photo.file.size, logo?.size ?? 0)
      + selected.reduce((total, file) => total + file.size, 0);
    if (aggregateBytes > 200 * 1024 * 1024) {
      setPhotoStatus("Original photographs and issuer assets cannot exceed 200 MB in one package.");
      return;
    }
    const imageFiles = selected.filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length !== selected.length) setPhotoStatus("Only image files were added.");
    else setPhotoStatus("");
    setPhotos((current) => [
      ...current,
      ...imageFiles.map((file) => ({
        id: crypto.randomUUID(),
        file,
        previewUrl: URL.createObjectURL(file),
        caption: file.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " "),
        captureDate: file.lastModified ? new Date(file.lastModified).toISOString().slice(0, 10) : "",
        isUnmodifiedOriginal: false,
      })),
    ]);
  };

  const updatePhoto = (id: string, changes: Partial<PhotoInput>) => {
    setPhotos((current) => current.map((photo) => (photo.id === id ? { ...photo, ...changes } : photo)));
  };

  const removePhoto = (id: string) => {
    setPhotos((current) => {
      const removed = current.find((photo) => photo.id === id);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((photo) => photo.id !== id);
    });
  };

  const generatePackage = async (values: FormValues) => {
    setReceipt(undefined);
    if (!identity) {
      setGenerationStatus("Generate or import a signing identity before issuing the certificate.");
      return;
    }
    if (!backupDownloaded) {
      setGenerationStatus("Download the encrypted signing-key backup before issuing the first certificate.");
      return;
    }
    if (photos.length === 0) {
      setGenerationStatus("Add at least one exact photograph of this specimen.");
      return;
    }
    if (photos.some((photo) => !photo.isUnmodifiedOriginal)) {
      setGenerationStatus("Confirm that every listed photograph is an unmodified original of this exact specimen.");
      return;
    }

    setGenerationBusy(true);
    setGenerationStatus("Rendering, hashing, signing, and packaging entirely in this browser...");
    try {
      const { buildCertificatePackage } = await import("./lib/package");
      const result = await buildCertificatePackage({ values, photos, logo, identity });
      downloadBlob(result.blob, result.fileName);
      setReceipt({ recordHash: result.recordHash, manifestHash: result.manifestHash });
      setGenerationStatus("The self-contained signed package was generated and downloaded.");
    } catch (error) {
      setGenerationStatus(error instanceof Error ? error.message : "The certificate package could not be generated.");
    } finally {
      setGenerationBusy(false);
    }
  };

  return (
    <>
      <a className="skip-link" href="#builder">Skip to certificate builder</a>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Spacerocks COA Studio home">
          <span className="brand__mark"><i /></span>
          <span><strong>Spacerocks</strong><small>COA Studio</small></span>
        </a>
        <nav aria-label="Primary navigation">
          <a href="#builder">Create</a>
          <a href="#verify">Verify</a>
          <a href="#method">Method</a>
        </nav>
        <span className="local-badge"><i /> Local-only cryptography</span>
      </header>

      <main id="top">
        <section className="hero">
          <div className="hero__orbit" aria-hidden="true"><i /><i /><i /></div>
          <div className="hero__content">
            <p className="eyebrow"><span>01</span> Evidence that outlives the website</p>
            <h1>A certificate is only as enduring as its <em>proof.</em></h1>
            <p className="hero__lead">Create a professional meteorite COA whose signature, specimen photographs, and file hashes can be verified offline, even if this site disappears.</p>
            <div className="hero__actions">
              <a className="button button--gold" href="#builder">Build a certificate</a>
              <a className="text-link" href="#verify">Verify an existing package <span aria-hidden="true">-&gt;</span></a>
            </div>
          </div>
          <div className="hero__ledger" aria-label="Self-contained proof chain">
            <div className="ledger__head"><span>Proof chain</span><small>COA / V1</small></div>
            <ol>
              <li><span>01</span><div><strong>Exact evidence</strong><small>Original photographs retained byte-for-byte</small></div><i /></li>
              <li><span>02</span><div><strong>SHA-256 manifest</strong><small>Every released evidence file measured</small></div><i /></li>
              <li><span>03</span><div><strong>Ed25519 signature</strong><small>Issuer-controlled key authorizes the record</small></div><i /></li>
              <li><span>04</span><div><strong>Offline verification</strong><small>Open formats, public key, and verifier included</small></div><i /></li>
            </ol>
            <div className="ledger__foot"><span>Website dependency</span><strong>NONE</strong></div>
          </div>
        </section>

        <section className="principles" aria-label="Core principles">
          <div><strong>01</strong><span><b>Private by design</b>Your signing key and photographs never leave this browser.</span></div>
          <div><strong>02</strong><span><b>Tamper evident</b>A one-byte change breaks the signed hash chain.</span></div>
          <div><strong>03</strong><span><b>Open and portable</b>JSON, PEM, text, PNG, PDF, and a plain Python verifier.</span></div>
        </section>

        <section className="builder-section" id="builder">
          <div className="section-heading">
            <p className="eyebrow eyebrow--dark"><span>02</span> Issue a self-contained record</p>
            <h2>Certificate workbench</h2>
            <p>Complete the record, attach exact source photographs, unlock your issuer identity, then export one signed verification package.</p>
          </div>

          <form className="builder-grid" onSubmit={handleSubmit(generatePackage, () => setGenerationStatus("Review the highlighted required fields."))}>
            <div className="workbench">
              <details className="workbench-section" open>
                <summary><span>01</span><div><strong>Issuer identity</strong><small>Who is authorizing this record</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Issuer display or legal name" error={errors.issuerName?.message}>
                    <input {...register("issuerName")} />
                  </Field>
                  <Field label="Collection or business" error={errors.collectionName?.message}>
                    <input {...register("collectionName")} />
                  </Field>
                  <Field label="Email" error={errors.issuerEmail?.message}>
                    <input type="email" {...register("issuerEmail")} />
                  </Field>
                  <Field label="Phone">
                    <input {...register("issuerPhone")} />
                  </Field>
                  <Field label="Address" wide>
                    <input {...register("issuerAddress")} />
                  </Field>
                  <Field label="Website" error={errors.issuerWebsite?.message}>
                    <input type="url" placeholder="https://" {...register("issuerWebsite")} />
                  </Field>
                  <Field label="Logo" hint="Optional. Included and hashed in the package.">
                    <input type="file" accept="image/*" onChange={(event) => setLogo(event.target.files?.[0])} />
                  </Field>
                </div>
              </details>

              <details className="workbench-section" open>
                <summary><span>02</span><div><strong>Certificate identity</strong><small>Versioned, traceable, never silently overwritten</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Certificate ID" hint="Portable characters only" error={errors.certificateId?.message}>
                    <input {...register("certificateId")} />
                  </Field>
                  <Field label="Issue date" error={errors.issueDate?.message}>
                    <input type="date" {...register("issueDate")} />
                  </Field>
                  <Field label="Version" error={errors.certificateVersion?.message}>
                    <input {...register("certificateVersion")} />
                  </Field>
                  <Field label="Status" error={errors.certificateStatus?.message}>
                    <select {...register("certificateStatus")}>
                      <option value="active">Active</option>
                      <option value="superseded">Superseded</option>
                      <option value="revoked">Revoked</option>
                      <option value="transferred">Transferred</option>
                    </select>
                  </Field>
                  <Field label="Superseded certificate ID" error={errors.supersededCertificateId?.message}>
                    <input {...register("supersededCertificateId")} />
                  </Field>
                  <Field label="Certificate notes">
                    <input {...register("certificateNotes")} />
                  </Field>
                  <fieldset className="theme-field field--wide">
                    <legend>Certificate color scheme</legend>
                    <span className="theme-field__hint">The selected palette is applied to the live preview, PNG, and PDF.</span>
                    <div className="theme-picker">
                      {certificateThemes.map((theme) => (
                        <label className="theme-option" key={theme.id}>
                          <input type="radio" value={theme.id} {...register("certificateTheme")} />
                          <span
                            className="theme-option__body"
                            style={{
                              "--swatch-dark": theme.dark,
                              "--swatch-accent": theme.accent,
                              "--swatch-paper": theme.paper,
                            } as React.CSSProperties}
                          >
                            <span className="theme-option__swatches" aria-hidden="true"><i /><i /><i /></span>
                            <strong>{theme.name}</strong>
                            <small>{theme.description}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                </div>
              </details>

              <details className="workbench-section" open>
                <summary><span>03</span><div><strong>Specimen record</strong><small>Physical identity and classification</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Meteorite name" error={errors.meteoriteName?.message}>
                    <input {...register("meteoriteName")} />
                  </Field>
                  <Field label="Classification" error={errors.classification?.message}>
                    <input {...register("classification")} />
                  </Field>
                  <Field label="Weight (grams)" error={errors.weightGrams?.message}>
                    <input inputMode="decimal" {...register("weightGrams")} />
                  </Field>
                  <Field label="Weight precision (grams)" error={errors.weightPrecision?.message}>
                    <input inputMode="decimal" {...register("weightPrecision")} />
                  </Field>
                  <Field label="Specimen form" error={errors.specimenForm?.message}>
                    <select {...register("specimenForm")}>
                      <option>Complete individual</option>
                      <option>Partial individual</option>
                      <option>Half stone / end cut</option>
                      <option>Slice</option>
                      <option>Fragment</option>
                      <option>Dust</option>
                      <option>Thin section</option>
                      <option>Other</option>
                    </select>
                  </Field>
                  <Field label="Dimensions">
                    <input placeholder="e.g. 42 x 31 x 18 mm" {...register("dimensions")} />
                  </Field>
                  <Field label="Number of pieces" error={errors.numberOfPieces?.message}>
                    <input inputMode="numeric" {...register("numberOfPieces")} />
                  </Field>
                  <Field label="Preparation state">
                    <input {...register("preparationState")} />
                  </Field>
                  <Field label="Identifying marks" wide>
                    <input {...register("identifyingMarks")} />
                  </Field>
                  <Field label="Recorded owner" error={errors.recordedOwner?.message}>
                    <input {...register("recordedOwner")} />
                  </Field>
                </div>
              </details>

              <details className="workbench-section">
                <summary><span>04</span><div><strong>Fall, find, and provenance</strong><small>Origin and chain of custody</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Fall or find status" error={errors.fallStatus?.message}>
                    <input {...register("fallStatus")} />
                  </Field>
                  <Field label="Date" error={errors.fallDate?.message}>
                    <input type="date" {...register("fallDate")} />
                  </Field>
                  <Field label="Country" error={errors.country?.message}>
                    <input {...register("country")} />
                  </Field>
                  <Field label="Region">
                    <input {...register("region")} />
                  </Field>
                  <Field label="Locality" error={errors.locality?.message}>
                    <input {...register("locality")} />
                  </Field>
                  <Field label="Meteoritical Bulletin code">
                    <input {...register("metbullCode")} />
                  </Field>
                  <Field label="Latitude" error={errors.latitude?.message}>
                    <input {...register("latitude")} />
                  </Field>
                  <Field label="Longitude" error={errors.longitude?.message}>
                    <input {...register("longitude")} />
                  </Field>
                  <Field label="Official reference URL" wide error={errors.officialReferenceUrl?.message}>
                    <input type="url" {...register("officialReferenceUrl")} />
                  </Field>
                  <Field label="Finder / recovery information" wide>
                    <textarea rows={3} {...register("recoveryInformation")} />
                  </Field>
                  <Field label="Provenance and chain of custody" wide error={errors.provenance?.message}>
                    <textarea rows={4} {...register("provenance")} />
                  </Field>
                  <Field label="Previous owner">
                    <input {...register("previousOwner")} />
                  </Field>
                  <Field label="Buyer / transferee">
                    <input {...register("buyer")} />
                  </Field>
                  <Field label="Transfer date">
                    <input type="date" {...register("transferDate")} />
                  </Field>
                  <Field label="Invoice / reference">
                    <input {...register("invoiceReference")} />
                  </Field>
                  <Field label="Transfer notes" wide>
                    <textarea rows={3} {...register("transferNotes")} />
                  </Field>
                </div>
              </details>

              <section className="evidence-section">
                <div className="evidence-section__head"><span>05</span><div><strong>Exact specimen photographs</strong><small>At least one unmodified original is mandatory</small></div></div>
                <label
                  className="photo-drop"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    addPhotos(event.dataTransfer.files);
                  }}
                >
                  <input type="file" accept="image/*" multiple onChange={(event) => addPhotos(event.target.files)} />
                  <span>+</span>
                  <strong>Drop exact source photographs here</strong>
                  <small>or choose files - JPEG, PNG, WebP, TIFF, or another browser-readable image</small>
                </label>
                {photoStatus ? <p className="inline-status inline-status--error">{photoStatus}</p> : null}
                <div className="photo-list">
                  {photos.map((photo, index) => (
                    <article className="photo-item" key={photo.id}>
                      <img src={photo.previewUrl} alt="" />
                      <div className="photo-item__fields">
                        <div className="photo-item__heading">
                          <div className="photo-item__meta"><span>Original {String(index + 1).padStart(2, "0")}</span><strong>{photo.file.name}</strong><small>{(photo.file.size / 1024 / 1024).toFixed(2)} MB</small></div>
                          <button type="button" className="remove-button" onClick={() => removePhoto(photo.id)} aria-label={`Remove ${photo.file.name}`}>Remove</button>
                        </div>
                        <label>Caption<input value={photo.caption} onChange={(event) => updatePhoto(photo.id, { caption: event.target.value })} /></label>
                        <label>Capture date<input type="date" value={photo.captureDate} onChange={(event) => updatePhoto(photo.id, { captureDate: event.target.value })} /></label>
                        <label className="attestation"><input type="checkbox" checked={photo.isUnmodifiedOriginal} onChange={(event) => updatePhoto(photo.id, { isUnmodifiedOriginal: event.target.checked })} /><span>I attest this is an exact, unmodified photograph of the specimen.</span></label>
                      </div>
                    </article>
                  ))}
                </div>
              </section>

              <section className="key-section">
                <div className="key-section__head">
                  <div><span>06</span><strong>Issuer signing identity</strong></div>
                  <span className={identity ? "key-state key-state--loaded" : "key-state"}><i />{identity ? "Key loaded" : "No key"}</span>
                </div>
                {identity ? (
                  <div className="loaded-key">
                    <span>Ed25519 public-key fingerprint</span>
                    <code>{identity.fingerprint}</code>
                    <div className="loaded-key__actions">
                      {encryptedBundle && !backupDownloaded ? <button type="button" className="button button--gold button--small" onClick={downloadKeyBackup}>Download encrypted key backup</button> : null}
                      <button type="button" className="text-button" onClick={() => downloadBlob(new Blob([identity.publicKeyPem], { type: "application/x-pem-file" }), "public-key.pem")}>Download public key</button>
                      <button type="button" className="text-button text-button--danger" onClick={forgetKey}>Forget key</button>
                    </div>
                  </div>
                ) : (
                  <div className="key-options">
                    <div className="key-option">
                      <span className="option-label">Create new identity</span>
                      <p>Generate a persistent issuer key, encrypted with a passphrase before it can be downloaded.</p>
                      <label>Passphrase<input type="password" autoComplete="new-password" value={generatePassphrase} onChange={(event) => setGeneratePassphrase(event.target.value)} /></label>
                      <label>Confirm passphrase<input type="password" autoComplete="new-password" value={confirmPassphrase} onChange={(event) => setConfirmPassphrase(event.target.value)} /></label>
                      <button type="button" className="button button--navy button--small" disabled={keyBusy} onClick={() => void generateKey()}>{keyBusy ? "Working..." : "Generate Ed25519 key"}</button>
                    </div>
                    <div className="key-divider"><span>or</span></div>
                    <div className="key-option">
                      <span className="option-label">Unlock existing identity</span>
                      <p>Import a Spacerocks encrypted key backup. The file and passphrase remain local.</p>
                      <label>Encrypted key backup<input type="file" accept=".json,application/json" onChange={(event) => setImportFile(event.target.files?.[0])} /></label>
                      <label>Passphrase<input type="password" autoComplete="current-password" value={importPassphrase} onChange={(event) => setImportPassphrase(event.target.value)} /></label>
                      <button type="button" className="button button--outline button--small" disabled={keyBusy} onClick={() => void importKey()}>{keyBusy ? "Working..." : "Unlock signing key"}</button>
                    </div>
                  </div>
                )}
                <p className="key-status" aria-live="polite">{keyStatus}</p>
                <div className="security-note"><strong>Private means private.</strong><span>No key, passphrase, image, or form value is sent to a server. This page has no application backend.</span></div>
              </section>

              <section className="issue-section">
                <div>
                  <span>Final release</span>
                  <h3>Build, sign, and package</h3>
                  <p>Creates PDF, PNG, text, deterministic JSON, original photos, hashes, signature, public key, schema, audit log, and offline verifier.</p>
                </div>
                <button className="button button--gold button--issue" type="submit" disabled={generationBusy}>{generationBusy ? "Building package..." : "Issue signed COA package"}</button>
                <p className="generation-status" aria-live="polite">{generationStatus}</p>
                {receipt ? (
                  <div className="release-receipt">
                    <strong>Release created</strong>
                    <span>Record SHA-256 <code>{receipt.recordHash}</code></span>
                    <span>Manifest SHA-256 <code>{receipt.manifestHash}</code></span>
                  </div>
                ) : null}
              </section>
            </div>

            <aside className="preview-column">
              <div className="preview-column__head"><span>Live preview</span><small>Final export is high resolution</small></div>
              <CertificatePreview values={previewValues} photo={photos[0]} identity={identity} />
              <div className="preview-checks">
                <span className={identity ? "complete" : ""}><i />Signing identity</span>
                <span className={photos.length ? "complete" : ""}><i />Original evidence</span>
                <span className={photos.length > 0 && photos.every((photo) => photo.isUnmodifiedOriginal) ? "complete" : ""}><i />Photo attestation</span>
                <span><i />Offline verifier included</span>
              </div>
              <div className="preview-note"><strong>PDF/A note</strong><p>The browser export is a standard PDF and is not represented as PDF/A until independently validated with veraPDF. PNG and UTF-8 text archival copies are included.</p></div>
            </aside>
          </form>
        </section>

        <section className="verify-section" id="verify">
          <div className="section-heading section-heading--light">
            <p className="eyebrow"><span>03</span> Trust, but verify</p>
            <h2>Check the proof in your browser.</h2>
            <p>Verification recalculates the cryptography from the package itself. A green report means the files are internally intact; independently confirm that the fingerprint belongs to the named issuer.</p>
          </div>
          <PackageVerifier />
        </section>

        <section className="method-section" id="method">
          <div className="section-heading">
            <p className="eyebrow eyebrow--dark"><span>04</span> The method</p>
            <h2>Four open checks. No permanent middleman.</h2>
          </div>
          <div className="method-grid">
            <article><span>01 / RECORD</span><h3>Describe the exact object</h3><p>A canonical certificate record binds identity, physical facts, provenance, and hashes of the original photographs.</p></article>
            <article><span>02 / HASH</span><h3>Measure every evidence file</h3><p>SHA-256 records the bytes and length of each certificate output, audit record, issuer asset, and exact source image.</p></article>
            <article><span>03 / SIGN</span><h3>Authorize the manifest</h3><p>The issuer signs the exact deterministic manifest with Ed25519. Only the public key enters the released package.</p></article>
            <article><span>04 / PRESERVE</span><h3>Carry the verifier with the proof</h3><p>The ZIP contains its schema, checksums, public key, instructions, and open Python verifier for use without this website.</p></article>
          </div>
          <div className="method-callout">
            <strong>Why the QR carries a record hash</strong>
            <p>A certificate image is itself listed in the signed manifest, so embedding that manifest's final hash inside the same image would create an impossible self-reference. The QR instead carries the hash of a separate canonical certificate record. The signed manifest binds that record and the final certificate outputs together.</p>
          </div>
        </section>
      </main>

      <footer>
        <div className="brand brand--footer"><span className="brand__mark"><i /></span><span><strong>Spacerocks</strong><small>COA Studio</small></span></div>
        <p>Self-contained meteorite records built with open formats and issuer-controlled cryptography.</p>
        <div><a href="#builder">Create</a><a href="#verify">Verify</a><a href="https://www.lpi.usra.edu/meteor/" target="_blank" rel="noreferrer">Meteoritical Bulletin</a></div>
      </footer>
    </>
  );
}
