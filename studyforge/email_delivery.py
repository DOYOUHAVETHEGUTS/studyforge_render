"""
Email delivery.

V2 fixes for the V1 email problem:
  * Pin the certifi CA bundle on the TLS context (fixes Windows OS-cert-store
    CERTIFICATE_VERIFY_FAILED, the diagnosed V1 failure).
  * diagnose() reproduces ssl_diagnostic.py so the UI can show why it broke.
  * The caller (pipeline.send_next) does NOT advance the queue on failure by
    default, so failures stop being silent.

V2.1: added a Resend-based HTTPS sender (email_backend="resend"). Antivirus/VPN
"scan encrypted mail" features intercept SMTP+STARTTLS specifically and present a
self-signed cert — no CA bundle can fix that. A plain HTTPS POST to Resend's API
(same transport as any other web request) sidesteps that interception entirely.
"""
import base64
import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def _context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def send(settings, subject, body_text, attachment_path):
    """Dispatch to the configured backend. Callers should use this, not the
    backend-specific functions directly, so switching backends in Settings just works."""
    backend = settings.get("email_backend", "smtp")
    if backend == "resend":
        send_via_resend(settings, subject, body_text, attachment_path)
    else:
        send_with_attachment(settings, subject, body_text, attachment_path)


def send_via_resend(settings, subject, body_text, attachment_path):
    import requests
    from . import config

    api_key = config.get_resend_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No Resend API key configured. Open Settings to add one, or export "
            f"{settings['resend_api_key_env']}.")
    to_email = settings.get("to_email")
    if not to_email:
        raise RuntimeError("settings.to_email must be set.")

    attachment_bytes = Path(attachment_path).read_bytes()
    payload = {
        "from": settings.get("resend_from") or "onboarding@resend.dev",
        "to": [to_email],
        "subject": subject,
        "text": body_text,
        "attachments": [{
            "filename": Path(attachment_path).name,
            "content": base64.b64encode(attachment_bytes).decode("ascii"),
        }],
    }
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text[:300]}")


def send_with_attachment(settings, subject, body_text, attachment_path):
    from . import config
    pw = config.get_email_password(settings)
    if not pw:
        raise RuntimeError(
            f"No email app password configured. Set it in Settings, or export "
            f"{settings['email_app_password_env']}.")
    if not settings.get("email_address") or not settings.get("to_email"):
        raise RuntimeError("settings.email_address and settings.to_email must be set.")

    msg = MIMEMultipart()
    msg["From"] = settings["email_address"]
    msg["To"] = settings["to_email"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))
    with open(attachment_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=Path(attachment_path).name)
    part["Content-Disposition"] = f'attachment; filename="{Path(attachment_path).name}"'
    msg.attach(part)

    with smtplib.SMTP(settings["smtp_server"], settings["smtp_port"], timeout=20) as s:
        s.starttls(context=_context())
        s.login(settings["email_address"], pw)
        s.send_message(msg)


def diagnose(settings):
    """3-step TLS probe used by the UI/CLI to explain failures."""
    host, port = settings["smtp_server"], settings["smtp_port"]
    results = {}

    def probe(label, ctx):
        try:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls(context=ctx)
            results[label] = "SUCCESS"
        except Exception as e:  # noqa: BLE001
            results[label] = f"FAILED: {e}"

    probe("os_store", ssl.create_default_context())
    try:
        import certifi
        probe("certifi", ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        results["certifi"] = "SKIPPED: certifi not installed"
    noverify = ssl.create_default_context()
    noverify.check_hostname = False
    noverify.verify_mode = ssl.CERT_NONE
    probe("no_verify", noverify)

    if results.get("certifi") == "SUCCESS" and results["os_store"].startswith("FAILED"):
        results["diagnosis"] = "OS cert store incomplete; certifi works. StudyForge pins certifi, so sending should succeed."
    elif all(v.startswith("FAILED") for k, v in results.items()
             if k in ("os_store", "certifi")) and results["no_verify"] == "SUCCESS":
        results["diagnosis"] = "TLS inspection (antivirus/VPN/proxy) is substituting a cert. Use an API sender or add the inspection CA."
    elif all(str(v).startswith(("FAILED", "SKIPPED")) for v in results.values()):
        results["diagnosis"] = "All probes failed: likely port 587 blocked or no network route."
    else:
        results["diagnosis"] = "TLS OK."
    return results
