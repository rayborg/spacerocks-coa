import { startTransition, useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import type {
  FormValues,
  PhotoInput,
  SigningIdentity,
  VerificationResult,
} from "./types";
import { downloadBlob, sanitizeFileName } from "./lib/core";
import { generateSigningIdentity, importSigningIdentity } from "./lib/crypto";
import {
  certificateThemes,
  getCertificateTheme,
} from "./certificateThemes";
import {
  certificateStyles,
  getCertificateStyle,
} from "./certificateStyles";
import PaidTimestampPanel from "./components/PaidTimestampPanel";
import { timestampServiceConfig } from "./lib/timestamp-service";
import { formSchema } from "./lib/form-validation";

const defaultValues: FormValues = {
  issuerName: "",
  collectionName: "",
  issuerEmail: "",
  issuerPhone: "",
  issuerAddress: "",
  issuerWebsite: "",
  certificateId: "",
  issueDate: "",
  certificateVersion: "",
  certificateStatus: "active",
  certificateStyle: "celestial-formal",
  certificateTheme: "observatory-navy",
  supersededCertificateId: "",
  certificateNotes: "",
  meteoriteIdentity: "unclassified",
  meteoriteName: "",
  meteoriteType: "Unclassified",
  classification: "Unclassified",
  meteoriteSubclass: "Unclassified",
  suspectedType: "",
  officialNameVerified: false,
  weightGrams: "",
  weightPrecision: "",
  specimenForm: "",
  dimensions: "",
  numberOfPieces: "",
  preparationState: "",
  identifyingMarks: "",
  recordedOwner: "",
  fallStatus: "",
  fallDate: "",
  country: "",
  region: "",
  locality: "",
  latitude: "",
  longitude: "",
  metbullCode: "",
  officialReferenceUrl: "",
  finderName: "",
  recoveryInformation: "",
  provenance: "",
  previousOwner: "",
  intermediaryPurchaserName: "",
  buyer: "",
  transferDate: "",
  invoiceReference: "",
  transferNotes: "",
};

function classificationSummary(values: FormValues): string {
  if (values.meteoriteIdentity === "unclassified") {
    return values.suspectedType.trim() ? `Unclassified - suspected ${values.suspectedType.trim()}` : "Unclassified";
  }
  return [values.meteoriteType, values.classification, values.meteoriteSubclass]
    .map((value) => value.trim())
    .filter(Boolean)
    .join(" / ") || "Official classification";
}

function locationSummary(values: FormValues): string {
  const parts = [values.locality, values.region, values.country]
    .map((value) => value.trim())
    .filter((value, index, all) => value && all.findIndex((candidate) => candidate.toLowerCase() === value.toLowerCase()) === index);
  return parts.join(", ") || "Not entered";
}

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
  logoPreviewUrl,
}: {
  values: FormValues;
  photo?: PhotoInput;
  identity?: SigningIdentity;
  logoPreviewUrl?: string;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const statusClass = values.certificateStatus === "active" ? "" : " certificate-preview--flagged";
  const theme = getCertificateTheme(values.certificateTheme);
  const certificateStyle = getCertificateStyle(values.certificateStyle);
  const hasWeight = Boolean(values.weightGrams.trim());
  const hasSpecimenForm = Boolean(values.specimenForm.trim());
  const specimenState = hasWeight && hasSpecimenForm ? "complete" : hasWeight || hasSpecimenForm ? "partial" : "empty";
  const isMuseumLedger = certificateStyle.id === "museum-ledger";
  const isMuseumType = certificateStyle.id === "museum-type";
  const museumCatalogNote = values.recoveryInformation.trim()
    || values.preparationState.trim()
    || values.provenance.trim()
    || "Not recorded";
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
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const updateScale = () => {
      const scale = Math.min(canvas.clientWidth / 1100, canvas.clientHeight / 850);
      if (scale > 0) canvas.style.setProperty("--certificate-preview-scale", scale.toString());
    };
    updateScale();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateScale);
      return () => window.removeEventListener("resize", updateScale);
    }

    const observer = new ResizeObserver(updateScale);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      className={`certificate-preview certificate-preview--${certificateStyle.id}${statusClass}`}
      style={themeStyle}
      data-certificate-style={certificateStyle.id}
      data-certificate-theme={theme.id}
      aria-label={`Live certificate preview in ${certificateStyle.name} style and ${theme.name} colors`}
    >
      <div className="certificate-preview__canvas" ref={canvasRef}>
        <div className="certificate-preview__frame">
          <header className="certificate-preview__header">
            <div className="certificate-preview__collection">
              {logoPreviewUrl ? <img className="certificate-preview__logo" src={logoPreviewUrl} alt={`${values.collectionName || "Collection"} logo`} /> : <span className="orbit-mark" aria-hidden="true"><i /></span>}
              <span>{values.collectionName || "Collection name"}</span>
            </div>
            <span className="certificate-preview__record-type">{isMuseumLedger ? "Signed specimen catalog" : isMuseumType ? "Scientific specimen identification" : "Archival specimen record"}</span>
            <strong>Certificate of Authenticity</strong>
            <div className="certificate-preview__id">
              <span>{isMuseumLedger ? "COA catalog ID" : isMuseumType ? "Specimen record" : "Certificate ID"}</span>
              <strong>{values.certificateId || "Pending"}</strong>
            </div>
          </header>
          <div className="certificate-preview__body">
            <div className="certificate-preview__title">
              <h3>{values.meteoriteName || "Meteorite name"}</h3>
              <p>{classificationSummary(values)}</p>
            </div>
            <div className="certificate-preview__photo">
              {photo ? <img src={photo.previewUrl} alt={photo.caption || "Uploaded specimen"} /> : <span>Exact specimen photo required</span>}
              {isMuseumLedger || isMuseumType ? (
                <small className="certificate-preview__photo-caption">
                  {isMuseumType ? "Specimen photo 01" : "Exact specimen / photo record 01"}
                </small>
              ) : null}
            </div>
            <dl className="certificate-preview__facts">
              <div><dt>Fall / find</dt><dd>{values.fallStatus || "Pending"}</dd></div>
              <div><dt>Location</dt><dd>{locationSummary(values)}</dd></div>
              <div><dt>Specimen form</dt><dd>{values.specimenForm || "Not recorded"}</dd></div>
              <div><dt>Recorded owner</dt><dd>{values.recordedOwner.trim() || "Not recorded"}</dd></div>
            </dl>
            {isMuseumType ? (
              <div className="certificate-preview__catalog-note">
                <span>Catalog notes</span>
                <p>{museumCatalogNote}</p>
              </div>
            ) : null}
            <div className={`certificate-preview__weight certificate-preview__weight--${specimenState}`} data-specimen-state={specimenState}>
              {specimenState === "empty" ? (
                <><span>Recorded specimen</span><strong>Awaiting details</strong></>
              ) : (
                <>
                  <span>{specimenState === "complete" ? "Specimen details" : hasWeight ? "Recorded weight" : "Specimen form"}</span>
                  <strong>{hasWeight ? <>{values.weightGrams}<small> g</small></> : values.specimenForm}</strong>
                  {specimenState === "complete" ? <em>{values.specimenForm}</em> : null}
                  {isMuseumType && values.dimensions.trim() ? <small className="certificate-preview__measurements">{values.dimensions}</small> : null}
                </>
              )}
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
    setValue,
    trigger,
    formState: { errors, isValid },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues,
    mode: "onChange",
  });
  const watchedValues = useWatch({ control });
  const meteoriteIdentity = watchedValues.meteoriteIdentity ?? defaultValues.meteoriteIdentity;
  const previewValues = useDeferredValue({ ...defaultValues, ...watchedValues } as FormValues);
  const resetOfficialAttestation = () => {
    if (getValues("officialNameVerified")) {
      setValue("officialNameVerified", false, { shouldDirty: true, shouldValidate: true });
    }
  };
  useEffect(() => {
    if (meteoriteIdentity === "official") {
      void trigger([
        "meteoriteType",
        "classification",
        "meteoriteSubclass",
        "metbullCode",
        "officialReferenceUrl",
        "officialNameVerified",
      ]);
    }
  }, [meteoriteIdentity, trigger]);
  useEffect(() => {
    void trigger(["locality", "region", "latitude", "longitude"]);
  }, [watchedValues.locality, watchedValues.region, watchedValues.latitude, watchedValues.longitude, trigger]);
  useEffect(() => {
    if (meteoriteIdentity === "official") void trigger("officialReferenceUrl");
  }, [meteoriteIdentity, watchedValues.metbullCode, trigger]);

  const [photos, setPhotos] = useState<PhotoInput[]>([]);
  const [logo, setLogo] = useState<File>();
  const [logoPreviewUrl, setLogoPreviewUrl] = useState<string>();
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
  const [selectedService, setSelectedService] = useState<"free" | "blockchain">("free");
  const [receipt, setReceipt] = useState<{ recordHash: string; manifestHash: string; certificateReference: string }>();
  const allPhotosAttested = photos.length > 0 && photos.every((photo) => photo.isUnmodifiedOriginal);
  const issueReady = isValid && Boolean(identity) && backupDownloaded && allPhotosAttested;
  useEffect(() => {
    if (!logo) {
      setLogoPreviewUrl(undefined);
      return;
    }

    const previewUrl = URL.createObjectURL(logo);
    setLogoPreviewUrl(previewUrl);
    return () => URL.revokeObjectURL(previewUrl);
  }, [logo]);
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
        caption: "",
        captureDate: "",
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
      setReceipt({ recordHash: result.recordHash, manifestHash: result.manifestHash, certificateReference: values.certificateId });
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
      </header>

      <main id="top">
        {timestampServiceConfig ? (
          <section className="service-options" id="coa-options" aria-labelledby="coa-options-heading">
            <div className="service-options__intro">
              <p className="eyebrow eyebrow--dark"><span>01</span> Choose your proof level</p>
              <h2 id="coa-options-heading">Choose how your COA is proven.</h2>
              <p>Create a signed COA for free, or add a permanent public Bitcoin commitment with independently verifiable proof for $9.99.</p>
            </div>
            <div className="service-options__grid">
              <article className={`service-option${selectedService === "free" ? " service-option--selected" : ""}`}>
                <h3>Cryptographically Signed COA</h3>
                <div className="service-option__price"><strong>$0</strong><span>Free · no account required</span></div>
                <p>Create, sign, download, and verify the complete certificate package locally.</p>
                <ul>
                  <li>Ed25519 + SHA-256 proof</li>
                  <li>Offline verifier included</li>
                  <li>No account or payment</li>
                </ul>
                <a className="button button--navy" href="#builder" onClick={() => setSelectedService("free")}>Create free cryptographically signed COA</a>
              </article>
              <article className={`service-option service-option--blockchain${selectedService === "blockchain" ? " service-option--selected" : ""}`}>
                <h3>Cryptographically Signed COA + Bitcoin-Anchored Proof</h3>
                <div className="service-option__price"><strong>$9.99</strong><span>One-time managed service</span></div>
                <p>Anchor a one-way commitment to the signed manifest digest and receive a downloadable verification proof.</p>
                <ul>
                  <li>Everything in the free COA</li>
                  <li>Bitcoin-anchored commitment</li>
                  <li>Downloadable .ots proof</li>
                </ul>
                <a
                  className="button button--gold"
                  href="#builder"
                  onClick={() => setSelectedService("blockchain")}
                >Choose Bitcoin-anchored proof</a>
              </article>
            </div>
            <p className="service-options__note">The Bitcoin anchor contains a one-way cryptographic commitment, not the COA or photographs.</p>
          </section>
        ) : null}

        <section className="hero">
          <div className="hero__orbit" aria-hidden="true"><i /><i /><i /></div>
          <div className="hero__content">
            <p className="eyebrow"><span>{timestampServiceConfig ? "02" : "01"}</span> Evidence that outlives the website</p>
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
            <div className="ledger__foot"><span>COA verification website dependency</span><strong>NONE</strong></div>
          </div>
          <a className="hero__scroll-cue" href="#principles">
            <span>Explore below</span>
            <i aria-hidden="true" />
          </a>
        </section>

        <section className="principles" id="principles" aria-label="Core principles">
          <div><strong>01</strong><span><b>Private by design</b>Your signing key and photographs never leave this browser.</span></div>
          <div><strong>02</strong><span><b>Tamper evident</b>A one-byte change breaks the signed hash chain.</span></div>
          <div><strong>03</strong><span><b>Open and portable</b>JSON, PEM, text, PNG, PDF, and a plain Python verifier.</span></div>
        </section>

        <section className="builder-section" id="builder">
          <div className="section-heading">
            <p className="eyebrow eyebrow--dark"><span>{timestampServiceConfig ? "03" : "02"}</span> Issue a self-contained record</p>
            <h2>Certificate workbench</h2>
            <p>Complete the record, attach exact source photographs, unlock your issuer identity, then export one signed verification package.</p>
            {timestampServiceConfig ? (
              <div className={`selected-service selected-service--${selectedService}`}>
                <span>Selected option</span>
                <strong>{selectedService === "blockchain" ? "Cryptographically Signed COA + Bitcoin-Anchored Proof · $9.99" : "Free cryptographically signed COA · $0"}</strong>
                <a href="#coa-options">Change option</a>
              </div>
            ) : null}
          </div>

          <form className="builder-grid" onSubmit={handleSubmit(generatePackage, () => setGenerationStatus("Review the highlighted required fields."))}>
            <div className="workbench">
              <details className="workbench-section" open>
                <summary><span>01</span><div><strong>Issuer identity</strong><small>Who is authorizing this record</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Issuer display or legal name" error={errors.issuerName?.message}>
                    <input required aria-required="true" placeholder="e.g., John Doe" {...register("issuerName")} />
                  </Field>
                  <Field label="Collection or business" error={errors.collectionName?.message}>
                    <input required aria-required="true" placeholder="e.g., Example Meteorite Collection" {...register("collectionName")} />
                  </Field>
                  <Field label="Email (optional)" error={errors.issuerEmail?.message}>
                    <input type="email" placeholder="Optional - e.g., issuer@example.com" {...register("issuerEmail")} />
                  </Field>
                  <Field label="Phone (optional)">
                    <input type="tel" placeholder="Optional - e.g., +1 555 010 0123" {...register("issuerPhone")} />
                  </Field>
                  <Field label="Address (optional)" wide>
                    <input placeholder="Optional - e.g., 123 Example Street, City, Country" {...register("issuerAddress")} />
                  </Field>
                  <Field label="Website (optional)" error={errors.issuerWebsite?.message}>
                    <input type="url" placeholder="Optional - e.g., https://example.com" {...register("issuerWebsite")} />
                  </Field>
                  <Field label="Logo (optional)" hint="Optional - included and hashed in the package.">
                    <input type="file" accept="image/*" onChange={(event) => setLogo(event.target.files?.[0])} />
                  </Field>
                </div>
              </details>

              <details className="workbench-section" open>
                <summary><span>02</span><div><strong>Certificate identity</strong><small>Versioned, traceable, never silently overwritten</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Certificate ID" hint="Portable characters only" error={errors.certificateId?.message}>
                    <input required aria-required="true" placeholder="e.g., COA-2026-0001" {...register("certificateId")} />
                  </Field>
                  <Field label="Issue date" error={errors.issueDate?.message}>
                    <input required aria-required="true" type="date" placeholder="e.g., 2026-07-29" {...register("issueDate")} />
                  </Field>
                  <Field label="Version" error={errors.certificateVersion?.message}>
                    <input required aria-required="true" placeholder="e.g., 1.0" {...register("certificateVersion")} />
                  </Field>
                  <Field label="Status" error={errors.certificateStatus?.message}>
                    <select {...register("certificateStatus")}>
                      <option value="active">Active</option>
                      <option value="superseded">Superseded</option>
                      <option value="revoked">Revoked</option>
                      <option value="transferred">Transferred</option>
                    </select>
                  </Field>
                  <Field label="Superseded certificate ID" hint="Required only when status is Superseded." error={errors.supersededCertificateId?.message}>
                    <input placeholder="Required only for Superseded status" {...register("supersededCertificateId")} />
                  </Field>
                  <Field label="Certificate notes (optional)">
                    <input placeholder="Optional - notes about this certificate version" {...register("certificateNotes")} />
                  </Field>
                  <fieldset className="theme-field field--wide">
                    <legend>Certificate template</legend>
                    <span className="theme-field__hint">Choose Celestial Formal for an astronomical presentation or Museum Type for a scientific specimen card.</span>
                    <div className="style-picker">
                      {certificateStyles.map((style) => (
                        <label className="style-option" key={style.id}>
                          <input type="radio" value={style.id} {...register("certificateStyle")} />
                          <span className={`style-option__body style-option__body--${style.id}`}>
                            <span className="style-option__sample" aria-hidden="true"><i /><i /></span>
                            <strong>{style.name}</strong>
                            <small>{style.description}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
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
                  <fieldset className="theme-field field--wide">
                    <legend>Meteorite identity</legend>
                    <span className="theme-field__hint">Choose Official only when you have checked the canonical Meteoritical Bulletin entry.</span>
                    <div className="style-picker">
                      <label className="style-option">
                        <input required type="radio" value="unclassified" {...register("meteoriteIdentity", { onChange: resetOfficialAttestation })} />
                        <span className="style-option__body"><strong>Unclassified</strong><small>Working specimen identity; official evidence is not exported.</small></span>
                      </label>
                      <label className="style-option">
                        <input required type="radio" value="official" {...register("meteoriteIdentity", { onChange: resetOfficialAttestation })} />
                        <span className="style-option__body"><strong>Official</strong><small>Canonical name and classification verified by the issuer.</small></span>
                      </label>
                    </div>
                  </fieldset>
                  <Field label={meteoriteIdentity === "official" ? "Official canonical meteorite name" : "Working specimen name"} error={errors.meteoriteName?.message}>
                    <input required aria-required="true" placeholder={meteoriteIdentity === "official" ? "e.g., Aguas Zarcas" : "e.g., Unclassified specimen 001"} {...register("meteoriteName", { onChange: resetOfficialAttestation })} />
                  </Field>
                  {meteoriteIdentity === "official" ? (
                    <>
                      <Field label="Meteorite type" error={errors.meteoriteType?.message}>
                        <input required aria-required="true" placeholder="e.g., Chondrite" {...register("meteoriteType", { onChange: resetOfficialAttestation })} />
                      </Field>
                      <Field label="Meteorite class" error={errors.classification?.message}>
                        <input required aria-required="true" placeholder="e.g., Carbonaceous chondrite" {...register("classification", { onChange: resetOfficialAttestation })} />
                      </Field>
                      <Field label="Meteorite subclass" error={errors.meteoriteSubclass?.message}>
                        <input required aria-required="true" placeholder="e.g., CM2" {...register("meteoriteSubclass", { onChange: resetOfficialAttestation })} />
                      </Field>
                    </>
                  ) : (
                    <Field label="Suspected type (optional)" hint="A working opinion only; exported classification remains Unclassified." wide>
                      <input placeholder="Optional - e.g., possible ordinary chondrite" {...register("suspectedType")} />
                    </Field>
                  )}
                  <Field label="Weight (grams)" error={errors.weightGrams?.message}>
                    <input required aria-required="true" inputMode="decimal" placeholder="e.g., 44.7" {...register("weightGrams")} />
                  </Field>
                  <Field label="Weight precision (grams)" error={errors.weightPrecision?.message}>
                    <input required aria-required="true" inputMode="decimal" placeholder="e.g., 0.1" {...register("weightPrecision")} />
                  </Field>
                  <Field label="Specimen form" error={errors.specimenForm?.message}>
                    <select required aria-required="true" {...register("specimenForm")}>
                      <option value="" disabled>Select specimen form</option>
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
                  <Field label="Dimensions (optional)">
                    <input placeholder="Optional - e.g., 42 x 31 x 18 mm" {...register("dimensions")} />
                  </Field>
                  <Field label="Number of pieces" error={errors.numberOfPieces?.message}>
                    <input required aria-required="true" inputMode="numeric" placeholder="e.g., 1" {...register("numberOfPieces")} />
                  </Field>
                  <Field label="Preparation state (optional)">
                    <input placeholder="Optional - e.g., natural crust with one cut face" {...register("preparationState")} />
                  </Field>
                  <Field label="Identifying marks (optional)" wide>
                    <input placeholder="Optional - e.g., collection label or distinguishing feature" {...register("identifyingMarks")} />
                  </Field>
                  <Field label="Recorded owner (optional)" error={errors.recordedOwner?.message}>
                    <input placeholder="Optional - e.g., John Doe" {...register("recordedOwner")} />
                  </Field>
                </div>
              </details>

              <details className="workbench-section">
                <summary><span>04</span><div><strong>Fall, find, and provenance</strong><small>Origin and chain of custody</small></div></summary>
                <div className="workbench-section__body field-grid">
                  <Field label="Fall or find status" error={errors.fallStatus?.message}>
                    <input required aria-required="true" placeholder="e.g., Witnessed fall" {...register("fallStatus")} />
                  </Field>
                  <Field label="Fall or find date (optional)" error={errors.fallDate?.message}>
                    <input type="date" placeholder="Optional - e.g., 2024-01-15" {...register("fallDate")} />
                  </Field>
                  <Field label="Country" error={errors.country?.message}>
                    <input required aria-required="true" placeholder="e.g., Canada" {...register("country")} />
                  </Field>
                  <Field label="Region (optional)">
                    <input placeholder="Optional - e.g., Ontario" {...register("region")} />
                  </Field>
                  <Field label="Locality / city (optional)" error={errors.locality?.message}>
                    <input placeholder="Optional - e.g., Near Example Township" {...register("locality")} />
                  </Field>
                  <Field label="Latitude (optional)" error={errors.latitude?.message}>
                    <input placeholder="Optional - decimal degrees, e.g., 45.4215 N or 45.4215" {...register("latitude")} />
                  </Field>
                  <Field label="Longitude (optional)" error={errors.longitude?.message}>
                    <input placeholder="Optional - decimal degrees, e.g., 75.6972 W or -75.6972" {...register("longitude")} />
                  </Field>
                  {meteoriteIdentity === "official" ? (
                    <>
                      <Field label="Meteoritical Bulletin code" error={errors.metbullCode?.message}>
                        <input required aria-required="true" inputMode="numeric" placeholder="e.g., 12345" {...register("metbullCode", { onChange: resetOfficialAttestation })} />
                      </Field>
                      <Field label="Official Meteoritical Bulletin URL" wide error={errors.officialReferenceUrl?.message}>
                        <input required aria-required="true" type="url" placeholder="https://www.lpi.usra.edu/meteor/metbull.cfm?code=12345" {...register("officialReferenceUrl", { onChange: resetOfficialAttestation })} />
                      </Field>
                      <Field
                        label="Official name verification"
                        wide
                        error={errors.officialNameVerified?.message}
                        hint="No lookup is performed. Open the official record and make this attestation yourself."
                      >
                        <span className="attestation">
                          <input required aria-required="true" type="checkbox" {...register("officialNameVerified")} />
                          <span>I attest that the canonical name, type, class, and subclass match the linked Meteoritical Bulletin entry.</span>
                        </span>
                        {/^[0-9]+$/.test((watchedValues.metbullCode ?? "").trim()) ? (
                          <a
                            className="text-link"
                            href={`https://www.lpi.usra.edu/meteor/metbull.cfm?code=${(watchedValues.metbullCode ?? "").trim()}`}
                            target="_blank"
                            rel="noreferrer"
                          >Open official Meteoritical Bulletin entry</a>
                        ) : null}
                      </Field>
                    </>
                  ) : null}
                  <Field label="Finder name (optional)">
                    <input placeholder="Optional - person credited with the find or recovery" {...register("finderName")} />
                  </Field>
                  <Field label="Finder / recovery information (optional)" wide>
                    <textarea rows={3} placeholder="Optional - describe who recovered the specimen and how" {...register("recoveryInformation")} />
                  </Field>
                  <Field label="Provenance and chain of custody (optional)" wide error={errors.provenance?.message}>
                    <textarea rows={4} placeholder="Optional - describe the documented custody history" {...register("provenance")} />
                  </Field>
                  <Field label="Previous owner (optional)">
                    <input placeholder="Optional - e.g., previous collector or institution" {...register("previousOwner")} />
                  </Field>
                  <Field label="Intermediary purchaser name (optional)">
                    <input placeholder="Optional - e.g., dealer or interim purchaser" {...register("intermediaryPurchaserName")} />
                  </Field>
                  <Field label="Buyer / transferee (optional)">
                    <input placeholder="Optional - e.g., receiving collector or institution" {...register("buyer")} />
                  </Field>
                  <Field label="Transfer date (optional)" error={errors.transferDate?.message}>
                    <input type="date" placeholder="Optional - e.g., 2026-07-29" {...register("transferDate")} />
                  </Field>
                  <Field label="Invoice / reference (optional)">
                    <input placeholder="Optional - e.g., INV-2026-0001" {...register("invoiceReference")} />
                  </Field>
                  <Field label="Transfer notes (optional)" wide>
                    <textarea rows={3} placeholder="Optional - record transfer terms or related notes" {...register("transferNotes")} />
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
                        <label>Caption (optional)<input placeholder="Optional - e.g., front face" value={photo.caption} onChange={(event) => updatePhoto(photo.id, { caption: event.target.value })} /></label>
                        <label>Capture date (optional)<input type="date" placeholder="Optional - e.g., 2026-07-29" value={photo.captureDate} onChange={(event) => updatePhoto(photo.id, { captureDate: event.target.value })} /></label>
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
                      <label>Passphrase<input type="password" placeholder="Enter at least 12 characters" autoComplete="new-password" value={generatePassphrase} onChange={(event) => setGeneratePassphrase(event.target.value)} /></label>
                      <label>Confirm passphrase<input type="password" placeholder="Re-enter the passphrase" autoComplete="new-password" value={confirmPassphrase} onChange={(event) => setConfirmPassphrase(event.target.value)} /></label>
                      <button type="button" className="button button--navy button--small" disabled={keyBusy} onClick={() => void generateKey()}>{keyBusy ? "Working..." : "Generate Ed25519 key"}</button>
                    </div>
                    <div className="key-divider"><span>or</span></div>
                    <div className="key-option">
                      <span className="option-label">Unlock existing identity</span>
                      <p>Import a Spacerocks encrypted key backup. The file and passphrase remain local.</p>
                      <label>Encrypted key backup<input type="file" accept=".json,application/json" onChange={(event) => setImportFile(event.target.files?.[0])} /></label>
                      <label>Passphrase<input type="password" placeholder="Enter the backup passphrase" autoComplete="current-password" value={importPassphrase} onChange={(event) => setImportPassphrase(event.target.value)} /></label>
                      <button type="button" className="button button--outline button--small" disabled={keyBusy} onClick={() => void importKey()}>{keyBusy ? "Working..." : "Unlock signing key"}</button>
                    </div>
                  </div>
                )}
                <p className="key-status" aria-live="polite">{keyStatus}</p>
                <div className="security-note"><strong>Private means private.</strong><span>Keys, passphrases, images, the COA package, and the full form record stay local. If you choose the Bitcoin-anchored proof service, checkout sends the certificate reference, manifest digest, contact email, and consent record to the service. Payment details go directly to Stripe, and later status or proof requests use your private recovery code.</span></div>
              </section>

              <section className="issue-section">
                <div>
                  <span>Final release</span>
                  <h3>Build, sign, and package</h3>
                  <p>Creates PDF, PNG, text, deterministic JSON, original photos, hashes, signature, public key, schema, audit log, and offline verifier.</p>
                </div>
                <div id="issuance-readiness" aria-live="polite">
                  <strong>{issueReady ? "Ready to issue." : "Complete these requirements before issuance:"}</strong>
                  {!issueReady ? (
                    <ul>
                      {!isValid ? <li>Complete the required form fields.</li> : null}
                      {!identity ? <li>Generate or import a signing identity.</li> : null}
                      {identity && !backupDownloaded ? <li>Download the encrypted signing-key backup.</li> : null}
                      {photos.length === 0 ? <li>Add at least one exact specimen photograph.</li> : null}
                      {photos.length > 0 && !allPhotosAttested ? <li>Attest every photograph is an unmodified original.</li> : null}
                    </ul>
                  ) : null}
                </div>
                {!isValid ? (
                  <button
                    className="button button--outline button--small"
                    type="button"
                    onClick={() => {
                      void trigger();
                      setGenerationStatus("Review the highlighted required fields.");
                    }}
                  >Review missing form fields</button>
                ) : null}
                <button
                  className="button button--gold button--issue"
                  type="submit"
                  aria-describedby="issuance-readiness"
                  disabled={generationBusy || !issueReady}
                >{generationBusy ? "Building package..." : selectedService === "blockchain" ? "Issue COA and continue to Bitcoin proof" : "Issue cryptographically signed COA package"}</button>
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
              <div className="preview-column__head"><span>Live preview</span></div>
              <div
                className="certificate-preview-viewport"
                tabIndex={0}
                aria-label="Scrollable live certificate preview"
                onKeyDown={(event) => {
                  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                  event.preventDefault();
                  event.currentTarget.scrollLeft += event.key === "ArrowRight" ? 80 : -80;
                }}
              >
                <CertificatePreview values={previewValues} photo={photos[0]} identity={identity} logoPreviewUrl={logoPreviewUrl} />
              </div>
              <div className="preview-checks">
                <span className={identity ? "complete" : ""}><i />Signing identity</span>
                <span className={photos.length ? "complete" : ""}><i />Original evidence</span>
                <span className={allPhotosAttested ? "complete" : ""}><i />Photo attestation</span>
                <span><i />Offline verifier included</span>
              </div>
              <div className="preview-note"><strong>PDF/A note</strong><p>The browser export is a standard PDF and is not represented as PDF/A until independently validated with veraPDF. PNG and UTF-8 text archival copies are included.</p></div>
            </aside>
          </form>
        </section>

        {timestampServiceConfig ? (
          <PaidTimestampPanel
            config={timestampServiceConfig}
            focusOnRelease={selectedService === "blockchain"}
            release={receipt ? {
              certificateReference: receipt.certificateReference,
              manifestSha256: receipt.manifestHash,
            } : undefined}
          />
        ) : null}

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
            <h2>Four open COA checks. No permanent verification middleman.</h2>
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
