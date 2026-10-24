"""Shared email adapter: ONE place to resolve SMTP credentials and run a
real connectivity/test-send, instead of the two parallel, independent
raw-SMTP implementations this repo had before (InviteSender/
send_interview_invites.py for candidate invites, SendCandidateMetricsEmail.py
for internal recruiter reports) -- both of which read credentials from
their OWN plaintext JSON config file (InviteSender/config/
invite_email_config.json, config/email_credentials.json) and never used
secrets_loader.py.

resolve_smtp_config() now PREFERS secrets_loader.get_secret() (so SMTP
credentials can live in .env or Key Vault like every other secret in
this repo), falling back to the legacy per-script JSON file ONLY if the
secrets_loader values aren't set -- fail-soft, same convention as every
other migration in this repo: a deployment that hasn't moved its SMTP
credentials over keeps working exactly as before. The two existing
scripts were updated to call this function instead of reading their own
JSON file directly, but their SENDING logic (MIME construction, .ics
attachments, summary reports) is untouched -- this is a credential-
resolution fix, not a rewrite of two already-working scripts.
"""

import json
import os
import smtplib
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_secret  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent
_LEGACY_CONFIG_PATHS = [
    _REPO_ROOT / "InviteSender" / "config" / "invite_email_config.json",
    _REPO_ROOT / "config" / "email_credentials.json",
]


def _legacy_fallback(key: str):
    """Reads one field from whichever legacy JSON config file has it,
    checked in a fixed order -- never raises if a file is missing or
    malformed, just returns None so the caller's own "missing secret"
    error message stays the one the admin actually sees."""
    field_names = {
        "SMTP_EMAIL": ("smtp_email", "sender_email"),
        "SMTP_PASSWORD": ("smtp_password", "app_password"),
        "SMTP_SERVER": ("smtp_server",),
        "SMTP_PORT": ("smtp_port",),
    }
    for path in _LEGACY_CONFIG_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for field in field_names.get(key, ()):
            if field in data:
                return data[field]
    return None


def resolve_smtp_config() -> dict:
    """Returns {email, password, server, port, source} where `source` is
    "secrets_loader" if every field came from get_secret(), "legacy_json"
    if any field had to fall back to a JSON file, or "incomplete" if a
    required field is missing from both. Never raises -- callers check
    `source` and the individual fields themselves."""
    values = {}
    sources = set()
    for key in ("SMTP_EMAIL", "SMTP_PASSWORD", "SMTP_SERVER", "SMTP_PORT"):
        value = get_secret(key)
        if value is not None:
            sources.add("secrets_loader")
        else:
            value = _legacy_fallback(key)
            if value is not None:
                sources.add("legacy_json")
        values[key] = value

    missing = [k for k, v in values.items() if v is None]
    source = "incomplete" if missing else ("secrets_loader" if sources == {"secrets_loader"} else "legacy_json")
    return {
        "email": values["SMTP_EMAIL"], "password": values["SMTP_PASSWORD"],
        "server": values["SMTP_SERVER"], "port": int(values["SMTP_PORT"]) if values["SMTP_PORT"] else None,
        "source": source, "missing": missing,
    }


def validate_config() -> list:
    """Returns a list of error strings (empty = valid) -- never includes
    a credential value."""
    config = resolve_smtp_config()
    if config["missing"]:
        return [f"Missing SMTP configuration: {', '.join(config['missing'])}."]
    return []


def test_connectivity() -> dict:
    """A real SMTP connect + STARTTLS + login -- never sends a message.
    Returns {outcome, error_category, detail, duration_ms} using the
    same vocabulary as azure_integrations.check_connection() (healthy/
    unhealthy/unknown + error_category), for a consistent status
    language across every config console in this app."""
    import time as _time

    start = _time.monotonic()
    config = resolve_smtp_config()
    if config["missing"]:
        return {
            "outcome": "unknown", "error_category": "configuration_invalid",
            "detail": f"Missing SMTP configuration: {', '.join(config['missing'])}.",
            "duration_ms": 0.0,
        }
    try:
        server = smtplib.SMTP(config["server"], config["port"], timeout=10)
        try:
            server.starttls()
            server.login(config["email"], config["password"])
        finally:
            server.quit()
        return {
            "outcome": "healthy", "error_category": None, "detail": "SMTP login succeeded.",
            "duration_ms": round((_time.monotonic() - start) * 1000, 1),
        }
    except smtplib.SMTPAuthenticationError as e:
        return {"outcome": "unhealthy", "error_category": "authentication_failed", "detail": _redact(str(e)), "duration_ms": round((_time.monotonic() - start) * 1000, 1)}
    except (smtplib.SMTPConnectError, ConnectionRefusedError, OSError) as e:
        return {"outcome": "unhealthy", "error_category": "endpoint_unreachable", "detail": _redact(str(e)), "duration_ms": round((_time.monotonic() - start) * 1000, 1)}
    except Exception as e:
        return {"outcome": "unhealthy", "error_category": "unknown_error", "detail": _redact(str(e)), "duration_ms": round((_time.monotonic() - start) * 1000, 1)}


def _redact(text: str) -> str:
    """Minimal redaction for SMTP error text -- strips anything that
    looks like it echoed the password back (some SMTP servers include
    the attempted credential in an auth-failure message)."""
    config = resolve_smtp_config()
    password = config.get("password")
    if password and password in text:
        text = text.replace(password, "[REDACTED]")
    return text[:500]


def send_email(to_address: str, subject: str, body: str) -> dict:
    """Sends ONE plain-text email through the resolved SMTP config.
    Raises on any failure -- callers (the test-send endpoint, or a real
    workflow) decide how to handle/report that; this function doesn't
    swallow errors, unlike the legacy scripts' broad try/except, since a
    config-console-triggered send should surface its real failure
    reason to the admin who clicked it."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    config = resolve_smtp_config()
    if config["missing"]:
        raise RuntimeError(f"Missing SMTP configuration: {', '.join(config['missing'])}.")

    msg = MIMEMultipart()
    msg["From"] = config["email"]
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP(config["server"], config["port"], timeout=15)
    try:
        server.starttls()
        server.login(config["email"], config["password"])
        server.sendmail(config["email"], to_address, msg.as_string())
    finally:
        server.quit()
    return {"to": to_address, "subject": subject, "from": config["email"]}
