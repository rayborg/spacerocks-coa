import { useEffect, useState } from "react";
import { downloadBlob } from "../lib/core";
import {
  checkoutAttemptMatches,
  createCheckoutAttempt,
  createTimestampService,
  forgetTimestampSession,
  isDeterministicCheckoutError,
  isTimestampAuthError,
  isValidTimestampEmail,
  loadTimestampSession,
  pollOrderStatus,
  saveTimestampSession,
  validateStatusToken,
  type CheckoutAttempt,
  type OrderStatus,
  type TimestampServiceConfig,
  type TimestampSession,
} from "../lib/timestamp-service";

interface PaidTimestampPanelProps {
  config: TimestampServiceConfig;
  release?: {
    certificateReference: string;
    manifestSha256: string;
  };
}

const terminalPaymentStates = new Set(["failed", "expired", "refunded", "disputed"]);

function isPollingComplete(status: OrderStatus): boolean {
  return terminalPaymentStates.has(status.paymentState)
    || (status.fulfillmentState === "bitcoin_verified" && status.proofAvailable)
    || status.fulfillmentState === "delivered"
    || status.fulfillmentState === "manual_review";
}

function statusCopy(status: OrderStatus): { label: string; detail: string; tone: string } {
  if (status.fulfillmentState === "manual_review") return { label: "Manual review", detail: "Automation has paused safely. A historical calendar-submission time, if shown, does not mean the current review is complete or Bitcoin-confirmed.", tone: "warning" };
  if (status.paymentState === "failed") return { label: "Payment failed", detail: "No Bitcoin timestamp is confirmed. Use Stripe or support guidance before retrying.", tone: "error" };
  if (status.paymentState === "expired") return { label: "Checkout expired", detail: "No Bitcoin timestamp is confirmed for this expired checkout.", tone: "error" };
  if (status.paymentState === "refunded") return { label: "Order refunded", detail: "A refund does not erase timestamp evidence that may already exist. Review proof availability below.", tone: "warning" };
  if (status.paymentState === "disputed") return { label: "Order disputed", detail: "The commercial order is under review. This does not by itself change existing timestamp evidence.", tone: "warning" };
  if (status.fulfillmentState === "bitcoin_verified" || status.fulfillmentState === "delivered") {
    if (!status.proofAvailable) {
      return { label: "Initial Bitcoin confirmation verified", detail: "The service verified at least one Bitcoin confirmation for the exact submitted manifest digest. This initial result remains subject to reorganization monitoring. A separate final confirmation email is sent only after stable evidence of at least six confirmations. The downloadable proof bundle is still being prepared.", tone: "confirmed" };
    }
    return { label: "Initial Bitcoin confirmation verified", detail: "The service verified at least one Bitcoin confirmation for the exact submitted manifest digest in the Bitcoin-attested OpenTimestamps proof. This initial result remains subject to reorganization monitoring. A separate final confirmation email is sent only after stable evidence of at least six confirmations.", tone: "confirmed" };
  }
  if (status.fulfillmentState === "calendar_pending") {
    return { label: "Calendar proof pending", detail: "Submitted to one or more public OpenTimestamps calendars. It is not yet Bitcoin-confirmed and may take hours or longer.", tone: "pending" };
  }
  if (status.fulfillmentState === "stamping") return { label: "Creating timestamp proof", detail: "Automation is submitting the exact manifest digest. This is not Bitcoin confirmation.", tone: "pending" };
  if (status.fulfillmentState === "queued" || status.paymentState === "paid") return { label: "Paid, awaiting timestamping", detail: "Payment is recorded, but no Bitcoin confirmation is claimed yet.", tone: "pending" };
  if (status.paymentState === "processing") return { label: "Payment processing", detail: "Payment is not yet settled. Timestamp fulfillment has not been confirmed.", tone: "pending" };
  return { label: "Checkout open", detail: "The browser return is not proof of payment. Complete checkout and wait for server status.", tone: "pending" };
}

function sessionFromStatus(token: string, status: OrderStatus, config: TimestampServiceConfig): TimestampSession {
  return {
    token,
    orderRef: status.orderReference,
    certificateReference: status.certificateReference,
    manifestSha256: status.manifestSha256,
    apiBase: config.baseUrl,
    apiVersion: config.contractVersion,
  };
}

function assertSessionBinding(status: OrderStatus, session: TimestampSession): void {
  if (status.orderReference !== session.orderRef
    || status.certificateReference !== session.certificateReference
    || status.manifestSha256 !== session.manifestSha256) {
    throw new Error("The service status did not match the locally saved order binding.");
  }
}

export default function PaidTimestampPanel({ config, release }: PaidTimestampPanelProps) {
  const [session, setSession] = useState<TimestampSession | undefined>(() => {
    try {
      return loadTimestampSession(config);
    } catch {
      return undefined;
    }
  });
  const [expanded, setExpanded] = useState(Boolean(release || session || window.location.hash === "#timestamp-service"));
  const [email, setEmail] = useState("");
  const [consented, setConsented] = useState(false);
  const [manualToken, setManualToken] = useState("");
  const [checkoutAttempt, setCheckoutAttempt] = useState<CheckoutAttempt>();
  const [orderStatus, setOrderStatus] = useState<OrderStatus>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [emailTouched, setEmailTouched] = useState(false);
  const [visible, setVisible] = useState(document.visibilityState !== "hidden");
  const [refreshKey, setRefreshKey] = useState(0);
  const emailValid = isValidTimestampEmail(email);
  const sandbox = config.mode === "sandbox";

  function acceptStatus(nextStatus: OrderStatus, activeSession: TimestampSession): void {
    assertSessionBinding(nextStatus, activeSession);
    setOrderStatus(nextStatus);
    if ((nextStatus.paymentState !== "checkout_open" || nextStatus.fulfillmentState !== "awaiting_payment") && activeSession.checkoutUrl) {
      const { checkoutUrl: _removed, ...withoutCheckout } = activeSession;
      saveTimestampSession(withoutCheckout, config);
      setSession(withoutCheckout);
    }
  }

  function handleStatusError(error: unknown): void {
    // Never leave a previously verified claim visible after malformed, contradictory, or failed refresh data.
    setOrderStatus(undefined);
    if (isTimestampAuthError(error)) {
      forgetTimestampSession();
      setSession(undefined);
      setMessage("The recovery code was rejected or revoked and has been removed from this tab.");
      return;
    }
    setMessage(error instanceof Error ? error.message : "The timestamp status could not be checked.");
  }

  useEffect(() => {
    const onVisibilityChange = () => {
      const nextVisible = document.visibilityState !== "hidden";
      setVisible(nextVisible);
      if (nextVisible) setRefreshKey((value) => value + 1);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  useEffect(() => {
    if (!session || !visible || (orderStatus && isPollingComplete(orderStatus))) return;
    const controller = new AbortController();
    const service = createTimestampService(config);
    setMessage("Checking the private order status...");
    void pollOrderStatus(service, session.token, {
      signal: controller.signal,
      maxAttempts: 6,
      stopWhen: isPollingComplete,
      onUpdate: (nextStatus) => {
        acceptStatus(nextStatus, session);
        setMessage(isPollingComplete(nextStatus) ? "Status is current." : "Status updated; bounded polling will continue while this page is visible.");
      },
    }).then((latest) => {
      if (!isPollingComplete(latest)) setMessage("Automatic checks paused after six attempts. Use Refresh status to check again.");
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      handleStatusError(error);
    });
    return () => controller.abort();
  // A visible-page transition or explicit refresh begins a new bounded polling window.
  }, [config, session, visible, refreshKey]);

  const createCheckout = async () => {
    if (!release) return;
    setEmailTouched(true);
    if (!emailValid) {
      setMessage(`A valid delivery email is required before creating ${sandbox ? "a test" : "the"} checkout.`);
      return;
    }
    if (!consented) {
      setMessage(`Explicit consent is required before creating ${sandbox ? "a test" : "the"} checkout.`);
      return;
    }
    setBusy(true);
    setMessage(sandbox ? "Creating a server-priced Stripe sandbox checkout..." : "Creating secure server-priced checkout...");
    const request = {
      certificateReference: release.certificateReference,
      manifestSha256: release.manifestSha256,
      email,
      policyVersion: config.policyVersion,
    };
    try {
      const attempt = checkoutAttempt && checkoutAttemptMatches(checkoutAttempt, request)
        ? checkoutAttempt
        : createCheckoutAttempt(request);
      setCheckoutAttempt(attempt);
      const result = await createTimestampService(config).createCheckout(attempt);
      const nextSession: TimestampSession = {
        token: result.statusToken,
        orderRef: result.orderReference,
        certificateReference: release.certificateReference,
        manifestSha256: release.manifestSha256,
        apiBase: config.baseUrl,
        apiVersion: config.contractVersion,
        checkoutUrl: result.checkoutUrl,
      };
      saveTimestampSession(nextSession, config);
      setSession(nextSession);
      setCheckoutAttempt(undefined);
      setMessage("Checkout is ready. Save the recovery code before continuing to Stripe.");
    } catch (error) {
      if (isDeterministicCheckoutError(error)) {
        setCheckoutAttempt(undefined);
        setMessage(error instanceof Error ? error.message : "Checkout was rejected.");
      } else {
        setMessage(`${error instanceof Error ? error.message : "The checkout result is uncertain."} Retry will reuse the exact request and idempotency key.`);
      }
    } finally {
      setBusy(false);
    }
  };

  const recover = async () => {
    setBusy(true);
    setMessage("Validating the recovery code without putting it in the URL...");
    try {
      const token = validateStatusToken(manualToken.trim());
      const status = await createTimestampService(config).getStatus(token);
      const nextSession = sessionFromStatus(token, status, config);
      saveTimestampSession(nextSession, config);
      setSession(nextSession);
      setOrderStatus(status);
      setManualToken("");
      setExpanded(true);
      setMessage("Recovery code stored for this browser tab only.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The order could not be recovered.");
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    if (!session) return;
    setBusy(true);
    setMessage("Refreshing status...");
    try {
      const status = await createTimestampService(config).getStatus(session.token);
      acceptStatus(status, session);
      setMessage("Status refreshed.");
    } catch (error) {
      handleStatusError(error);
    } finally {
      setBusy(false);
    }
  };

  const rotate = async () => {
    if (!session) return;
    setBusy(true);
    setMessage("Rotating the recovery code...");
    try {
      // Confirm storage is writable before the service irrevocably revokes the old token.
      saveTimestampSession(session, config);
      const replacement = await createTimestampService(config).rotateToken(session.token);
      const nextSession = { ...session, token: replacement };
      setSession(nextSession);
      try {
        saveTimestampSession(nextSession, config);
      } catch {
        setMessage("Recovery code rotated, but browser storage failed. Copy or download the visible replacement code now; the previous code is revoked.");
        return;
      }
      setMessage("Recovery code rotated. Save the replacement; the previous code is revoked.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The recovery code could not be rotated.");
    } finally {
      setBusy(false);
    }
  };

  const forget = () => {
    forgetTimestampSession();
    setSession(undefined);
    setOrderStatus(undefined);
    setCheckoutAttempt(undefined);
    setMessage("The recovery code was removed from this browser tab. The server order was not deleted.");
  };

  const copyRecoveryCode = async () => {
    if (!session) return;
    try {
      await navigator.clipboard.writeText(session.token);
      setMessage("Recovery code copied. Treat it like a private download credential.");
    } catch {
      setMessage("Clipboard access was blocked. Select the recovery code and copy it manually.");
    }
  };

  const downloadRecoveryCode = () => {
    if (!session) return;
    const text = [
      "Spacerocks managed timestamp recovery code",
      "Keep this private. Anyone with this code may view status or download an available proof.",
      `Order: ${session.orderRef}`,
      `Certificate: ${session.certificateReference}`,
      `Manifest SHA-256: ${session.manifestSha256}`,
      `Recovery code: ${session.token}`,
      `API base: ${session.apiBase}`,
      `Contract: ${session.apiVersion}`,
      "",
    ].join("\n");
    downloadBlob(new Blob([text], { type: "text/plain;charset=utf-8" }), "spacerocks-timestamp-recovery.txt");
    setMessage("Recovery file downloaded. Store it privately.");
  };

  const downloadProof = async () => {
    if (!session || !orderStatus?.proofAvailable) return;
    setBusy(true);
    setMessage("Downloading the separate timestamp proof bundle...");
    try {
      const proof = await createTimestampService(config).downloadProof(session.token, session.certificateReference);
      downloadBlob(proof.blob, proof.fileName);
      setMessage("Timestamp proof downloaded. Keep it with the original manifest; the signed COA ZIP remains unchanged.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The timestamp proof could not be downloaded.");
    } finally {
      setBusy(false);
    }
  };

  if (!expanded && !release && !session) {
    return (
      <section className="timestamp-recovery-launch" aria-label="Managed timestamp order recovery">
        <span>Already have a private recovery code?</span>
        <button type="button" className="text-button" onClick={() => setExpanded(true)}>Recover a timestamp order</button>
      </section>
    );
  }

  const displayStatus = orderStatus ? statusCopy(orderStatus) : undefined;
  const proofAllowed = Boolean(orderStatus?.proofAvailable
    && ["calendar_pending", "bitcoin_verified", "delivered"].includes(orderStatus.fulfillmentState)
    && ["paid", "refunded", "disputed"].includes(orderStatus.paymentState));

  return (
    <section className="timestamp-section" id="timestamp-service" aria-labelledby="timestamp-heading">
      <div className="timestamp-section__heading">
        <div>
          <p className="eyebrow eyebrow--dark"><span>+</span> Optional supplemental service</p>
          <h2 id="timestamp-heading">Managed Bitcoin timestamp</h2>
        </div>
        <strong className="timestamp-test-badge">{sandbox ? "Sandbox / test only" : "Live paid service"}</strong>
      </div>

      <div className="timestamp-explainer">
        <p><strong>Your signed COA is already complete.</strong> It remains independently verifiable without this service, Stripe, OpenTimestamps, Bitcoin, or this website.</p>
        {sandbox
          ? <p>Public OpenTimestamps calendars are free. A future fee would cover managed checkout, automation, monitoring, proof retention and upgrades, delivery, support, and related operations. The server controls any price; no amount is editable here.</p>
          : <p>Public OpenTimestamps calendars are free. The service fee covers managed checkout, automation, confirmation monitoring, proof retention and upgrades, delivery, support, and related operations. The server controls the price; no amount is editable here.</p>}
        <p>The separate proof anchors the exact submitted <code>manifest.json</code> SHA-256 digest through an aggregate commitment. It does not put the COA or photographs on Bitcoin, usually does not provide a unique transaction, and does not prove authenticity, ownership, identity, authorship, provenance truth, or an exact creation time.</p>
        <p>Initial Bitcoin verification requires at least one confirmation and remains subject to reorganization monitoring. A separate final confirmation email is sent only after stable evidence of at least six confirmations.</p>
      </div>

      {session ? (
        <div className="timestamp-order">
          <div className="timestamp-order__identity">
            <span>Private order reference</span>
            <strong>{session.orderRef}</strong>
            <small>{session.certificateReference}</small>
            <code>{session.manifestSha256}</code>
          </div>
          <div className="timestamp-recovery-code">
            <label htmlFor="timestamp-saved-token">Recovery code</label>
            <input id="timestamp-saved-token" readOnly value={session.token} spellCheck={false} autoComplete="off" />
            <small>Stored only in this tab's session storage. It is never placed in a URL, path, analytics event, console message, or download filename.</small>
            <div className="timestamp-actions">
              <button type="button" className="button button--outline button--small" onClick={() => void copyRecoveryCode()}>Copy code</button>
              <button type="button" className="button button--outline button--small" onClick={downloadRecoveryCode}>Download recovery file</button>
            </div>
          </div>
          {session.checkoutUrl ? (
            <div className="timestamp-checkout-ready">
              <strong>Recovery saved?</strong>
              <p>Continue only when you can recover this order. Stripe's return page is not proof of payment or Bitcoin confirmation.</p>
              <a className="button button--gold" href={session.checkoutUrl} referrerPolicy="no-referrer">{sandbox ? "Continue to Stripe sandbox" : "Continue to secure checkout"}</a>
            </div>
          ) : null}
          {displayStatus ? (
            <div className={`timestamp-status timestamp-status--${displayStatus.tone}`} role="status">
              <span>{displayStatus.label}</span>
              <p>{displayStatus.detail}</p>
              {orderStatus?.bitcoinVerifiedAt && (orderStatus.fulfillmentState === "bitcoin_verified" || orderStatus.fulfillmentState === "delivered")
                ? <small>Service verification time: {new Date(orderStatus.bitcoinVerifiedAt).toLocaleString()}</small>
                : null}
            </div>
          ) : null}
          <div className="timestamp-actions timestamp-actions--order">
            <button type="button" className="button button--navy button--small" disabled={busy} onClick={() => void refresh()}>Refresh status</button>
            <button type="button" className="button button--outline button--small" disabled={busy} onClick={() => void rotate()}>Rotate recovery code</button>
            <button type="button" className="button button--outline button--small" disabled={busy || !proofAllowed} onClick={() => void downloadProof()}>Download proof bundle</button>
            <button type="button" className="text-button text-button--danger" onClick={forget}>Forget this order</button>
          </div>
          {!proofAllowed ? (
            <p className="timestamp-proof-note">
              {orderStatus?.fulfillmentState === "bitcoin_verified"
                ? "Bitcoin verification metadata is current, but the separate proof bundle is still being prepared. Download will become available only after the server persists the matching bundle."
                : "Proof download remains unavailable until the server explicitly reports a deliverable proof. Pending never means Bitcoin-confirmed."}
            </p>
          ) : null}
        </div>
      ) : release ? (
        <div className="timestamp-checkout-form">
          <div className="timestamp-release-binding">
            <span>Released certificate</span>
            <strong>{release.certificateReference}</strong>
            <code>{release.manifestSha256}</code>
          </div>
          <label className="timestamp-field" htmlFor="timestamp-email">
            <span>Delivery email <strong>(required)</strong></span>
            <input
              id="timestamp-email"
              type="email"
              required
              aria-required="true"
              aria-invalid={emailTouched && !emailValid}
              aria-describedby={`timestamp-email-help${emailTouched && !emailValid ? " timestamp-email-error" : ""}`}
              maxLength={254}
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              onBlur={() => setEmailTouched(true)}
              placeholder="customer@example.com"
            />
            <small id="timestamp-email-help">Paid-service delivery contact. This is separate from the optional issuer email in the local COA. Only this email, the certificate reference, exact manifest digest, and consent record are sent.</small>
            {emailTouched && !emailValid ? <span className="timestamp-field__error" id="timestamp-email-error" role="alert">Enter a valid delivery email of 254 characters or fewer.</span> : null}
          </label>
          <label className="timestamp-consent">
            <input
              type="checkbox"
              checked={consented}
              onChange={(event) => {
                setConsented(event.target.checked);
                if (!event.target.checked) setCheckoutAttempt(undefined);
              }}
            />
            {config.mode === "production" ? (
              <span>I explicitly consent to managed timestamp processing under the <a href={config.termsUrl} referrerPolicy="no-referrer">Terms</a> and <a href={config.privacyUrl} referrerPolicy="no-referrer">Privacy Policy</a>, version <strong>{config.policyVersion}</strong>, including service retention of the email, certificate reference, manifest digest, payment/order records, and proof lifecycle data.</span>
            ) : (
              <span>I explicitly consent to sandbox managed timestamp processing under terms and privacy policy version <strong>{config.policyVersion}</strong>, including service retention of the email, certificate reference, manifest digest, payment/order records, and proof lifecycle data.</span>
            )}
          </label>
          <button type="button" className="button button--gold" disabled={busy || !consented || !emailValid} onClick={() => void createCheckout()}>{busy ? (sandbox ? "Creating test checkout..." : "Creating checkout...") : (sandbox ? "Create server-priced test checkout" : "Create secure checkout")}</button>
        </div>
      ) : (
        <div className="timestamp-manual-recovery">
          <h3>Recover an existing order</h3>
          <p>The code stays local until sent only in an Authorization header to the configured service. It is not added to this page's URL.</p>
          <label className="timestamp-field" htmlFor="timestamp-manual-token">
            <span>Private recovery code</span>
            <input id="timestamp-manual-token" type="password" autoComplete="off" spellCheck={false} value={manualToken} onChange={(event) => setManualToken(event.target.value)} />
          </label>
          <button type="button" className="button button--navy" disabled={busy || !manualToken.trim()} onClick={() => void recover()}>Recover in this tab</button>
        </div>
      )}

      <p className="timestamp-live-message" aria-live="polite">{message}</p>
      <p className="timestamp-privacy-note"><strong>Local boundary:</strong> private signing keys, passphrases, images, the COA package, manifest contents, addresses, provenance, and the full form record stay in this browser. The optional request sends only the fields listed above after explicit consent.</p>
      {config.mode === "production" ? (
        <p className="timestamp-privacy-note">Service policies: <a href={config.termsUrl} referrerPolicy="no-referrer">Terms</a>, <a href={config.privacyUrl} referrerPolicy="no-referrer">Privacy</a>, and <a href={config.refundUrl} referrerPolicy="no-referrer">Refund Policy</a>. Support: <a href={`mailto:${config.supportEmail}`}>{config.supportEmail}</a>.</p>
      ) : null}
    </section>
  );
}
