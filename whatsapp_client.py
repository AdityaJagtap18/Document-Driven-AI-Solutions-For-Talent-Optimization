"""WhatsApp Business Cloud API client — rebuilds the lost n8n WhatsApp
notification flow (see docs/azure-provisioning/phase5-document-intelligence-whatsapp.md
for why: the original n8n flow's logic is unrecovered, access was lost
before this engagement started).

Direct HTTP calls to Meta's Graph API via `requests`, not Azure Logic
Apps. This was a deliberate deviation from the original roadmap wording
("Azure Logic Apps"), for the same reason Azure Functions was chosen for
the API layer: this is testable in this environment (no Azure CLI/Logic
Apps designer available) and consistent with the Azure Functions pattern
already built in azure-functions/. A Logic App JSON workflow authored
blind, with zero way to validate it, would have been materially weaker
work than this.

Requires a Meta Developer app + WhatsApp Business phone number — see the
provisioning doc. Falls back gracefully (raises a clear error, caught by
callers) if not configured, same pattern as every other Azure integration
in this repo.
"""

import requests

from secrets_loader import get_secret

GRAPH_API_VERSION = "v21.0"


def _require_config():
    phone_number_id = get_secret("WHATSAPP_PHONE_NUMBER_ID")
    access_token = get_secret("WHATSAPP_ACCESS_TOKEN")
    missing = [n for n, v in [("WHATSAPP_PHONE_NUMBER_ID", phone_number_id), ("WHATSAPP_ACCESS_TOKEN", access_token)] if not v]
    if missing:
        raise RuntimeError(
            f"Missing required secret(s): {', '.join(missing)}. "
            "Add them to your .env file (see .env.example) or Key Vault, once the WhatsApp "
            "Business Cloud API app is provisioned — see "
            "docs/azure-provisioning/phase5-document-intelligence-whatsapp.md."
        )
    return phone_number_id, access_token


def send_whatsapp_text(to_phone_number: str, message: str) -> dict:
    """Sends a free-text WhatsApp message. NOTE: Meta only allows free-text
    messages within a 24-hour window of the recipient messaging you first;
    outside that window, a pre-approved message TEMPLATE is required (see
    send_whatsapp_template below). For a cold outbound notification (e.g.
    "your application status changed"), send_whatsapp_template is almost
    always what you actually want — this function exists for completeness
    and for use within an active conversation window."""
    phone_number_id, access_token = _require_config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone_number,
        "type": "text",
        "text": {"body": message},
    }
    response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    response.raise_for_status()
    return response.json()


def send_whatsapp_template(to_phone_number: str, template_name: str, language_code: str = "en_US", parameters: list = None) -> dict:
    """Sends a pre-approved WhatsApp message template — required for any
    notification outside an active 24-hour conversation window, which
    covers essentially every use case here (interview invites, status
    updates). `template_name` must already exist and be APPROVED in the
    Meta Business Manager (see the provisioning doc) — this call will fail
    with a clear Meta API error if it doesn't. `parameters` fills the
    template's {{1}}, {{2}}, ... placeholders in order, as plain strings."""
    phone_number_id, access_token = _require_config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"

    components = []
    if parameters:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in parameters],
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components,
        },
    }
    response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    response.raise_for_status()
    return response.json()
