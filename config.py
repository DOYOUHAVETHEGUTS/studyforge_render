"""
Central configuration.

Key V2 fix vs V1: everything is relative to the project root, so the repo runs
anywhere (not hard-coded to D:\\notes\\...). Secrets come from environment
variables only and are never written to disk.
"""
import json
import os
from pathlib import Path

# Project root = repo root (parent of this package dir)
ROOT = Path(__file__).resolve().parent.parent
# DATA_DIR defaults to <repo>/data for local use, but can be pointed at a mounted
# persistent disk in deployment (e.g. Render) via STUDYFORGE_DATA_DIR.
DATA_DIR = Path(os.environ.get("STUDYFORGE_DATA_DIR", ROOT / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"          # copied source files
EXTRACTED_DIR = DATA_DIR / "extracted"      # extracted section text
SUMMARIES_DIR = DATA_DIR / "summaries"      # generated .md / .pdf
UNSENT_DIR = DATA_DIR / "unsent"            # email-failed fallback copies
CONFIG_DIR = Path(os.environ.get("STUDYFORGE_CONFIG_DIR", ROOT / "config"))
DB_PATH = DATA_DIR / "studyforge.db"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
# Secrets live under data/ (already gitignored), separate from settings.json so the
# non-secret config can still be safely shared/committed if a user wants to.
SECRETS_PATH = DATA_DIR / "secrets.json"

for d in (DATA_DIR, UPLOADS_DIR, EXTRACTED_DIR, SUMMARIES_DIR, UNSENT_DIR, CONFIG_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    # Account
    "account_name": "",
    "account_email": "",
    # LLM
    "anthropic_api_key_env": "ANTHROPIC_API_KEY",
    "model": "claude-sonnet-5",
    "max_tokens": 8000,
    "max_retries": 3,
    # Email
    "email_backend": "smtp",           # "smtp" | "resend" | "none"
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "email_address": "",
    "to_email": "",
    "email_app_password_env": "STUDYFORGE_EMAIL_APP_PASSWORD",
    "resend_api_key_env": "STUDYFORGE_RESEND_API_KEY",
    "resend_from": "onboarding@resend.dev",
    # Processing preferences
    "summary_depth": "standard",       # "brief" | "standard" | "deep"
    "output_format": "both",           # "md" | "pdf" | "both"
    "make_learning_artifacts": True,
    # Delivery
    "advance_queue_on_email_failure": False,   # V2 fix: do NOT hide failures
    "grounding_check": True,
    # Custom agent prompts — optional per-agent overrides. A blank override means
    # "use the built-in default" (see agents.py). Two ways to set an override:
    #   1. Paste finalized prompt text directly (works for any Console prompt).
    #   2. Set the agent_id from Claude Managed Agents (agent_...) and hit "Sync" in
    #      Settings — StudyForge fetches that agent's system prompt via the Managed
    #      Agents API and stores it here. StudyForge still runs the fast, stateless
    #      Messages API per section — it does not run your agents as Managed Agent
    #      sessions (that runtime is built for tool-using/sandboxed work and bills
    #      per session-hour, which buys nothing for a single-shot completion).
    "agent1_structure_label": "",
    "agent1_structure_id": "",
    "agent1_structure_prompt": "",
    "agent2_summarize_label": "",
    "agent2_summarize_id": "",
    "agent2_summarize_prompt": "",
    "agent3_learning_label": "",
    "agent3_learning_id": "",
    "agent3_learning_prompt": "",
    "agent4_grounding_label": "",
    "agent4_grounding_id": "",
    "agent4_grounding_prompt": "",
    # Scheduled delivery — fires while `python -m studyforge.cli serve` is running (an
    # in-process background loop, not a system cron). See scheduler.py / README for the
    # OS-Task-Scheduler alternative for always-on delivery when the app isn't kept open.
    "schedule_cadence": "off",         # "off" | "daily" | "weekly"
    "schedule_time": "09:00",          # 24h HH:MM, local server time
    "schedule_weekday": 0,             # 0=Monday .. 6=Sunday, used only if weekly
    "schedule_last_run": "",           # ISO timestamp, set by the scheduler itself
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            user = json.load(f)
        return {**DEFAULT_SETTINGS, **user}
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def get_api_key(settings: dict) -> str:
    """Secrets file takes priority over the env var (lets the Settings UI work
    without requiring the user to set shell env vars). Never logged, never
    returned in full via the API."""
    secrets = load_secrets()
    return secrets.get("anthropic_api_key") or os.environ.get(settings["anthropic_api_key_env"], "")


def get_email_password(settings: dict) -> str:
    secrets = load_secrets()
    return secrets.get("email_app_password") or os.environ.get(settings["email_app_password_env"], "")


def get_resend_api_key(settings: dict) -> str:
    secrets = load_secrets()
    return secrets.get("resend_api_key") or os.environ.get(settings["resend_api_key_env"], "")


# --------------------------------------------------------------------------
# Secrets store — server-side only.
#
# MVP security decision: this is a single-user, no-auth local app (see
# README "Security & limitations"), so there is no per-user credential
# vault yet. Secrets are written to data/secrets.json — outside the repo's
# committed tree (data/ is gitignored), never rendered in full to the
# browser (only a last-4-chars mask), never logged, and never referenced
# from any frontend JS. File permissions are tightened to owner-only where
# the OS supports it (chmod 600; a no-op on Windows, which relies on NTFS
# per-user ACLs on the user's own profile directory instead).
# If/when multi-user support is added, this must become a per-user,
# encrypted-at-rest store — documented as a known limitation, not solved
# here to avoid building unneeded auth infrastructure.
# --------------------------------------------------------------------------
def load_secrets() -> dict:
    if SECRETS_PATH.exists():
        with open(SECRETS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_secrets(secrets: dict) -> None:
    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        json.dump(secrets, f, indent=2)
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass  # not supported on this OS/filesystem — non-fatal


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "•" * 8 + value[-4:] if len(value) > 4 else "•" * len(value)


_SECRET_FIELDS = ("anthropic_api_key", "email_app_password", "resend_api_key")


def settings_for_display(settings: dict) -> dict:
    """Settings dict safe to render in a template — secrets replaced with masks,
    never sent to the browser in full."""
    secrets = load_secrets()
    out = dict(settings)
    for f in _SECRET_FIELDS:
        out[f] = mask_secret(secrets.get(f, ""))
    return out


def apply_settings_update(current: dict, form: dict) -> dict:
    """Merge a submitted settings form into non-secret settings. Secret fields are
    handled separately by update_secrets_from_form (they never touch settings.json)."""
    updated = dict(current)
    checkboxes = ("advance_queue_on_email_failure", "grounding_check", "make_learning_artifacts")
    for key, value in form.items():
        if key in _SECRET_FIELDS or key not in DEFAULT_SETTINGS:
            continue
        if key in ("smtp_port", "max_tokens", "max_retries", "schedule_weekday"):
            try:
                updated[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif key in checkboxes:
            updated[key] = value in (True, "true", "on", "1", 1)
        else:
            updated[key] = value
    # HTML forms omit unchecked checkboxes entirely, not send "false" — reset those explicitly.
    for cb in checkboxes:
        if cb not in form:
            updated[cb] = False
    return updated


def update_secrets_from_form(form: dict) -> None:
    """A secret field left blank (or still showing its mask) keeps the existing
    stored value — this is what lets Settings display a masked key without the
    user having to re-paste it on every save."""
    secrets = load_secrets()
    changed = False
    for key in _SECRET_FIELDS:
        value = form.get(key, "")
        if value and not value.startswith("•"):
            secrets[key] = value
            changed = True
    if changed:
        save_secrets(secrets)


def readiness(settings: dict) -> dict:
    """Checks the UI shows before letting the user kick off work that needs them."""
    ai_ok = bool(get_api_key(settings))
    backend = settings.get("email_backend")
    if backend == "none":
        email_ok = True
    elif backend == "resend":
        email_ok = bool(get_resend_api_key(settings) and settings.get("to_email"))
    else:  # smtp
        email_ok = bool(
            settings.get("email_address") and settings.get("to_email") and get_email_password(settings))
    storage_ok = os.access(DATA_DIR, os.W_OK)
    return {"ai": ai_ok, "email": email_ok, "storage": storage_ok,
            "all_ready": ai_ok and storage_ok}


def rel(path) -> str:
    """Store paths relative to ROOT so the DB stays portable."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def abspath(relpath: str) -> Path:
    """Resolve a stored relative path back to absolute."""
    p = Path(relpath)
    return p if p.is_absolute() else (ROOT / p)
