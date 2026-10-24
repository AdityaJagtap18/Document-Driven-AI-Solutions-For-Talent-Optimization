"""The Secrets Management page's backend: a code-defined CATALOG of every
secret this repository actually reads (via secrets_loader.get_secret()),
same "code-defined catalog instead of a database" convention this app
already uses for auth.PAGES and azure_integrations.ADAPTER_TYPES.

VERIFIED AGAINST THE REPOSITORY -- SECRET_DEFINITIONS was built by grepping
every get_secret("...") / _require_secrets([...]) call site in this repo,
not invented or copied from .env.example. If a secret isn't read anywhere
in the code, it isn't listed here, even if it appears as a placeholder in
.env.example or docs.

This module NEVER returns a secret's resolved value -- only whether it
currently resolves to a non-empty value (`present`), and, when it does,
WHICH source answered it (`source`: "key_vault" or "environment"). That
distinction needs its own resolver (`secrets_loader.get_secret_with_source()`)
because secrets_loader.get_secret() itself only returns the value, not
where it came from -- this module doesn't duplicate that lookup logic, it
calls the same shared resolver.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_secret_with_source, set_env_secret  # noqa: E402

# Each entry: key (the exact os.environ / Key Vault name), label, secret
# (False only for non-sensitive config like a region code or a database
# name that happens to be read through the same resolver), used_by (the
# real call sites, so an administrator can see what breaks if it's
# missing), description.
SECRET_DEFINITIONS = [
    {"key": "JWT_SECRET", "label": "Session signing secret", "secret": True,
     "used_by": ["auth.py"], "description": "Signs/verifies every login session token. Required -- auth.py raises a clear error if missing."},
    {"key": "OPENAI_API_KEY", "label": "OpenAI API key", "secret": True,
     "used_by": ["ResumeParser.py", "interview_bot.py", "generate_questions.py", "voicebot/voice_bot.py", "semantic-library/embeddings.py"],
     "description": "Chat completions and embeddings via plain OpenAI -- used when Azure OpenAI (below) is not fully configured."},
    {"key": "AZURE_OPENAI_ENDPOINT", "label": "Azure OpenAI resource endpoint", "secret": False,
     "used_by": ["llm_utils.py", "semantic-library/embeddings.py"],
     "description": "When this, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_CHAT_DEPLOYMENT are all set, every chat/embedding call in this repo routes here instead of plain OpenAI."},
    {"key": "AZURE_OPENAI_API_KEY", "label": "Azure OpenAI API key", "secret": True,
     "used_by": ["llm_utils.py", "semantic-library/embeddings.py"], "description": "Authenticates to the Azure OpenAI resource above."},
    {"key": "AZURE_OPENAI_API_VERSION", "label": "Azure OpenAI API version", "secret": False,
     "used_by": ["llm_utils.py", "semantic-library/embeddings.py"], "description": "Defaults to 2024-10-21 if unset."},
    {"key": "AZURE_OPENAI_CHAT_DEPLOYMENT", "label": "Azure OpenAI chat deployment name", "secret": False,
     "used_by": ["llm_utils.py"], "description": "The deployed chat model's deployment name (not necessarily the underlying model's own name)."},
    {"key": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "label": "Azure OpenAI embedding deployment name", "secret": False,
     "used_by": ["semantic-library/embeddings.py"], "description": "Separate from the chat deployment -- required for embeddings to use Azure once chat is already configured for it."},
    {"key": "AZURE_LOG_ANALYTICS_WORKSPACE_ID", "label": "Log Analytics workspace ID", "secret": False,
     "used_by": ["azure_monitor_query.py"], "description": "The workspace's customerId GUID (not its resource name) -- required for the Azure Observability Report page to query real ingested telemetry back from Azure Monitor."},
    {"key": "COSMOS_ENDPOINT", "label": "Cosmos DB endpoint", "secret": False,
     "used_by": ["context-lake/client.py"], "description": "Primary data store connection -- see Azure Service Hub's cosmos_db integration."},
    {"key": "COSMOS_KEY", "label": "Cosmos DB primary key", "secret": True,
     "used_by": ["context-lake/client.py"], "description": "Primary data store authentication."},
    {"key": "COSMOS_DATABASE", "label": "Cosmos DB database name", "secret": False,
     "used_by": ["context-lake/client.py"], "description": "Which database within the Cosmos account this app uses."},
    {"key": "BLOB_CONNECTION_STRING", "label": "Blob Storage connection string", "secret": True,
     "used_by": ["context-lake/client.py"], "description": "Raw/parsed resume file storage -- see Azure Service Hub's blob_storage integration."},
    {"key": "DOCUMENT_INTELLIGENCE_ENDPOINT", "label": "Document Intelligence endpoint", "secret": False,
     "used_by": ["document_intelligence_client.py"], "description": "Certificate/marksheet OCR endpoint."},
    {"key": "DOCUMENT_INTELLIGENCE_KEY", "label": "Document Intelligence API key", "secret": True,
     "used_by": ["document_intelligence_client.py"], "description": "Certificate/marksheet OCR authentication."},
    {"key": "AZURE_SPEECH_KEY", "label": "Azure Speech API key", "secret": True,
     "used_by": ["voicebot/azure_speech_utils.py"], "description": "Voicebot interview text-to-speech/speech-to-text authentication."},
    {"key": "AZURE_SPEECH_REGION", "label": "Azure Speech region", "secret": False,
     "used_by": ["voicebot/azure_speech_utils.py"], "description": "Azure region the Speech resource is deployed in."},
    {"key": "APPLICATIONINSIGHTS_CONNECTION_STRING", "label": "Application Insights connection string", "secret": True,
     "used_by": ["observability.py"], "description": "Optional telemetry sink for LLM call and AI trace records."},
    {"key": "GMAIL_WATCHER_USER", "label": "Gmail watcher account", "secret": True,
     "used_by": ["file_watcher.py"], "description": "Mailbox the resume-intake watcher polls."},
    {"key": "GMAIL_WATCHER_APP_PASSWORD", "label": "Gmail watcher app password", "secret": True,
     "used_by": ["file_watcher.py"], "description": "App-specific password for the Gmail watcher account."},
    {"key": "WHATSAPP_PHONE_NUMBER_ID", "label": "WhatsApp Business phone number ID", "secret": True,
     "used_by": ["whatsapp_client.py"], "description": "Meta WhatsApp Business Cloud API sender identity."},
    {"key": "WHATSAPP_ACCESS_TOKEN", "label": "WhatsApp Business access token", "secret": True,
     "used_by": ["whatsapp_client.py"], "description": "Meta WhatsApp Business Cloud API authentication."},
    {"key": "SMTP_EMAIL", "label": "SMTP sender account", "secret": True,
     "used_by": ["InviteSender/send_interview_invites.py", "SendCandidateMetricsEmail.py", "email_adapter.py"],
     "description": "The email account used to send candidate invites and internal recruiter reports. "
                     "Preferred over the legacy per-script plaintext JSON config files (invite_email_config.json, "
                     "email_credentials.json), which remain as a fail-soft fallback."},
    {"key": "SMTP_PASSWORD", "label": "SMTP app password", "secret": True,
     "used_by": ["InviteSender/send_interview_invites.py", "SendCandidateMetricsEmail.py", "email_adapter.py"],
     "description": "App-specific password for SMTP_EMAIL."},
    {"key": "SMTP_SERVER", "label": "SMTP server host", "secret": False,
     "used_by": ["InviteSender/send_interview_invites.py", "SendCandidateMetricsEmail.py", "email_adapter.py"],
     "description": "SMTP server hostname (e.g. smtp.gmail.com)."},
    {"key": "SMTP_PORT", "label": "SMTP server port", "secret": False,
     "used_by": ["InviteSender/send_interview_invites.py", "SendCandidateMetricsEmail.py", "email_adapter.py"],
     "description": "SMTP server port (e.g. 587 for STARTTLS)."},
]
SECRET_KEYS = [s["key"] for s in SECRET_DEFINITIONS]


def update_secret_value(key: str, value: str) -> dict:
    """Writes `value` for `key` to .env (secrets_loader.set_env_secret())
    -- the only write path this module offers, and the only one the
    admin API exposes. Validates `key` is one of SECRET_KEYS first (this
    page never lets an admin write an arbitrary environment variable
    name). Returns the new status for that key -- present/source only,
    NEVER the value, same as every other read from this module."""
    if key not in SECRET_KEYS:
        raise ValueError(f"Unknown secret key '{key}'. Must be one of: {', '.join(SECRET_KEYS)}.")
    if not value:
        raise ValueError("value must be a non-empty string.")
    set_env_secret(key, value)
    definition = next(d for d in SECRET_DEFINITIONS if d["key"] == key)
    new_value, source = get_secret_with_source(key)
    return dict(definition, present=new_value is not None and new_value != "", source=source)


def list_secret_status() -> list:
    """Returns one status dict per SECRET_DEFINITIONS entry:
    {key, label, secret, used_by, description, present, source}.
    `present` is a real, live check (same get_secret() every real call
    site uses) -- never cached, never assumed. `source` is "key_vault",
    "environment", or None (not found anywhere) -- NEVER the value
    itself, in this field or any other."""
    result = []
    for definition in SECRET_DEFINITIONS:
        value, source = get_secret_with_source(definition["key"])
        result.append(dict(definition, present=value is not None and value != "", source=source if value else None))
    return result
