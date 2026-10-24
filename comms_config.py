"""Communications Configuration Console's backend: a channel-adapter
registry (CHANNELS, mirroring azure_integrations.ADAPTER_TYPES'
established pattern) plus real, versioned, lifecycle-managed
configuration for email/WhatsApp delivery policies and the voice
interview bot, kept as TWO SEPARATE lifecycle documents per the spec's
explicit "keep interview evaluation/bot config separate from
communication configuration."

VERIFIED AGAINST THE REPOSITORY:
- Email: TWO separate, parallel raw-SMTP scripts existed before this
  module (InviteSender/send_interview_invites.py, SendCandidateMetricsEmail.py),
  neither sharing code, both reading credentials from their own plaintext
  JSON file. email_adapter.py (see its docstring) is the new shared
  credential-resolution + connectivity/test-send adapter both scripts now
  call into for credentials -- their own sending logic is untouched.
- WhatsApp: whatsapp_client.py is a real, working Meta Graph API client,
  genuinely wired into api_blueprint.py's candidate-decision notification
  flow -- not a dead stub.
- SMS: zero code anywhere in this repo. Listed here with status
  "absent", not "planned" -- there is no SMS provider client, not even
  an unprovisioned one, unlike Azure AI Search/Foundry in the RAG
  console which at least had a real stub.
- Interview bot: voicebot/voice_bot.py + azure_speech_utils.py, real
  OpenAI + Azure Speech calls, with model/temperature/max_tokens/system-
  prompt/warm-up-question-count all hardcoded before this module.

FAIL-SOFT, same convention as every Cosmos-backed config in this repo:
if Cosmos is unavailable or nothing has been activated,
get_comms_runtime_config()/get_interview_bot_runtime_config() fall back
to these exact DEFAULT_* constants (today's hardcoded behavior).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "context-lake"))

CHANNELS = {
    "email": {
        "channel": "email", "status": "supported", "category": "Email",
        "description": "Raw SMTP via smtplib. Credentials resolve through email_adapter.py "
                        "(secrets_loader.py first, falling back to the legacy per-script JSON config files).",
        "source_modules": ["email_adapter.py", "InviteSender/send_interview_invites.py", "SendCandidateMetricsEmail.py"],
        "capabilities": {"connectivity_check": True, "test_message": True},
    },
    "whatsapp": {
        "channel": "whatsapp", "status": "supported", "category": "WhatsApp",
        "description": "Meta WhatsApp Business Cloud API (whatsapp_client.py) -- genuinely wired into "
                        "the candidate-decision notification workflow, not a stub.",
        "source_modules": ["whatsapp_client.py"],
        # No test_message: Meta's own rules mean a "test send" either goes through an
        # APPROVED TEMPLATE (which exists to notify real candidates, not to be spent on
        # tests) or requires an active 24h conversation window this app doesn't have with
        # any test number -- there's no safe sandbox send path, so this is honestly False,
        # not implemented as a fake success.
        "capabilities": {"connectivity_check": True, "test_message": False},
    },
    "sms": {
        "channel": "sms", "status": "absent", "category": "SMS",
        "description": "No SMS provider integration exists anywhere in this repository -- no Twilio, "
                        "no SNS, no provider client of any kind, not even an unprovisioned stub.",
        "source_modules": [],
        "capabilities": {"connectivity_check": False, "test_message": False},
        "requires_development_work": True,
    },
}
CHANNEL_IDS = list(CHANNELS.keys())

DEFAULT_COMMS_CONFIG = {
    "email": {"enabled": True, "sender_display_name": "CDPI Recruitment", "reply_to": ""},
    "whatsapp": {"enabled": True},
    "delivery_policies": {
        "default_channel": {"candidate_decision_notification": "whatsapp"},
        "duplicate_send_window_minutes": 60,
    },
}

VALID_RANGES = {
    "delivery_policies.duplicate_send_window_minutes": (1, 1440),
}

DEFAULT_INTERVIEW_BOT_CONFIG = {
    "model": "gpt-3.5-turbo",
    "temperature": 0.7,
    "max_tokens": 1000,
    "warmup_question_count": 3,
    "language": "en",
}
INTERVIEW_BOT_VALID_RANGES = {
    "temperature": (0.0, 2.0),
    "max_tokens": (50, 4000),
    "warmup_question_count": (1, 10),
}

# Seeded version 1 (reuses the REAL template this repo already has --
# InviteSender/config/interview_invite_template.json's exact body, not
# an invented one). WhatsApp has no admin-editable template body here --
# Meta requires templates to be authored and approved in Business
# Manager; this console only maps a workflow name to an approved
# template name (see repositories.get_whatsapp_template_mapping()).
DEFAULT_TEMPLATES = {
    "candidate_interview_invite": {
        "channel": "email",
        "subject": "Interview Scheduled with CDIP Recruitment – {candidate_name}",
        "body": "Dear {candidate_name},\n\nWe are pleased to invite you for an interview as part of the next "
                "steps in our recruitment process.\n\n Date: {interview_date} ({interview_day})\n Time: "
                "{interview_start} – {interview_end}\n Slot: {interview_slot}\n Interview Link: {interview_link}\n\n"
                "Please find your calendar invite attached. Ensure your availability during the mentioned slot.\n\n"
                "Best wishes,\nGroup 3 – DY Patil College of Engineering\nCognitive Resume Intelligence",
        "input_variables": ["candidate_name", "interview_date", "interview_day", "interview_start", "interview_end", "interview_slot", "interview_link"],
        "source_file": "InviteSender/config/interview_invite_template.json",
    },
}
TEMPLATE_IDS = list(DEFAULT_TEMPLATES.keys())

# Synthetic example data for template previews -- never real candidate
# data (see api_blueprint.py's preview_comms_template_endpoint).
TEMPLATE_PREVIEW_EXAMPLE_DATA = {
    "candidate_name": "Jordan Example", "interview_date": "15-11-2026", "interview_day": "Sunday",
    "interview_start": "10:00 AM", "interview_end": "10:45 AM", "interview_slot": "Slot 3",
    "interview_link": "https://meet.jit.si/Interview_Jordan_Example",
}


def _get(d: dict, dotted_key: str, default=None):
    node = d
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def validate_comms_config(config: dict) -> list:
    errors = []
    for dotted_key, (lo, hi) in VALID_RANGES.items():
        value = _get(config, dotted_key)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"'{dotted_key}' must be a number.")
        elif not (lo <= value <= hi):
            errors.append(f"'{dotted_key}' must be between {lo} and {hi} (got {value}).")

    default_channel = _get(config, "delivery_policies.default_channel") or {}
    for workflow, channel in default_channel.items():
        adapter = CHANNELS.get(channel)
        if adapter is None:
            errors.append(f"Unknown channel '{channel}' for workflow '{workflow}'.")
        elif adapter["status"] != "supported":
            errors.append(f"Channel '{channel}' for workflow '{workflow}' is '{adapter['status']}', not supported -- cannot be a default delivery channel.")
    return errors


def validate_interview_bot_config(config: dict) -> list:
    errors = []
    for key, (lo, hi) in INTERVIEW_BOT_VALID_RANGES.items():
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"'{key}' must be a number.")
        elif not (lo <= value <= hi):
            errors.append(f"'{key}' must be between {lo} and {hi} (got {value}).")
    return errors


def get_comms_runtime_config() -> dict:
    """Fails soft to DEFAULT_COMMS_CONFIG -- callers (api_blueprint.py's
    candidate notification flow) read this once per call, never
    assuming Cosmos is available."""
    try:
        import repositories

        doc = repositories.get_active_comms_config()
        if doc is None:
            return DEFAULT_COMMS_CONFIG
        return {"email": doc.get("email", DEFAULT_COMMS_CONFIG["email"]),
                "whatsapp": doc.get("whatsapp", DEFAULT_COMMS_CONFIG["whatsapp"]),
                "delivery_policies": doc.get("delivery_policies", DEFAULT_COMMS_CONFIG["delivery_policies"])}
    except Exception:
        return DEFAULT_COMMS_CONFIG


def get_interview_bot_runtime_config() -> dict:
    try:
        import repositories

        doc = repositories.get_active_interview_bot_config()
        if doc is None:
            return DEFAULT_INTERVIEW_BOT_CONFIG
        return {k: doc.get(k, v) for k, v in DEFAULT_INTERVIEW_BOT_CONFIG.items()}
    except Exception:
        return DEFAULT_INTERVIEW_BOT_CONFIG


def get_active_template(template_id: str) -> dict:
    defaults = DEFAULT_TEMPLATES.get(template_id)
    if defaults is None:
        raise ValueError(f"Unknown template_id '{template_id}'.")
    try:
        import repositories

        active = repositories.get_active_comms_template_version(template_id)
        if active is None:
            return dict(defaults, version="default")
        return {"channel": defaults["channel"], "subject": active.get("subject", ""), "body": active["body"],
                "input_variables": defaults["input_variables"], "version": active["version"]}
    except Exception:
        return dict(defaults, version="default")
