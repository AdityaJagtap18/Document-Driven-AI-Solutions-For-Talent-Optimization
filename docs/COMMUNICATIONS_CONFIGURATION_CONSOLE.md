# Communications Configuration Console

A real, backend-connected configuration console (`/admin/communications-config`)
for email, WhatsApp, SMS, and the voice interview bot — reusing the Draft →
Validate → Activate → Rollback lifecycle already built three times this
session (Azure, RAG, now Communications).

## Integration inventory (verified against the repository)

| Channel | Status | Evidence |
|---|---|---|
| **Email** | ✅ Implemented, previously fragmented | **Two separate, parallel raw-SMTP scripts** — `InviteSender/send_interview_invites.py` (candidate invites + `.ics` attachments) and `SendCandidateMetricsEmail.py` (internal recruiter/TA reports) — neither sharing code, both reading credentials from their own plaintext JSON config file, neither using `secrets_loader.py`. |
| **WhatsApp** | ✅ Genuinely implemented and wired in | `whatsapp_client.py` — real Meta Graph API client, properly uses `secrets_loader.get_secret()`. **Actually called** from `api_blueprint.py`'s candidate-decision notification flow — not dead code. |
| **SMS** | ❌ **Absent** | Zero code anywhere — no Twilio, no SNS, no provider reference of any kind. |
| **Interview bot (voice)** | ✅ Implemented, zero configurability (before this pass) | `voicebot/voice_bot.py` + `azure_speech_utils.py` — real OpenAI + Azure Speech calls, model/temperature/max_tokens/warm-up-question-count all hardcoded. `interview_bot.py` is a separate CLI scoring tool, out of scope per the spec's own "keep evaluation separate" rule. |

## What this pass built, all genuinely connected

1. **`email_adapter.py`** — the first shared credential-resolution + connectivity/test-send layer for email. `resolve_smtp_config()` now **prefers `secrets_loader.get_secret()`** (so SMTP credentials can live in `.env`/Key Vault like every other secret in this repo), falling back to the legacy per-script JSON files only if unset — fail-soft, verified live in this sandbox: a real SMTP login succeeded via the legacy-JSON fallback path, confirming the migration is backward-compatible. Both existing scripts were updated to call this for credentials only — their sending logic is untouched.
2. **`comms_config.py`** — a channel-adapter registry (`CHANNELS`) matching `azure_integrations.ADAPTER_TYPES`'s established pattern, plus a real, versioned lifecycle config for email/WhatsApp/delivery-policies and a **separate** lifecycle config for the interview bot (per the spec's explicit "keep bot config separate from communication configuration" rule).
3. **Real duplicate-send protection** — `repositories.has_recent_delivery()` is checked inside `_notify_candidate_whatsapp()` (the actual call site) before every real send; a retried request within the configured window is skipped, not re-sent.
4. **Real delivery logging** — every real WhatsApp send attempt (sent or failed) is recorded to `comms_delivery_log`, distinguishing `provider_accepted` from `failed` — never `delivered`, since no delivery-status webhook exists anywhere in this repo to justify that stronger claim.
5. **`whatsapp_client.check_connection()`** — a real Graph API metadata GET, never a message send (verified via direct testing in this sandbox: correctly reports `unknown`/`configuration_invalid` with no credentials configured).
6. **Versioned message templates**, seeded byte-for-byte from the real `InviteSender/config/interview_invite_template.json` content — editing and activating a version changes what the admin console's preview/test-send paths use; previews always render against clearly-labeled synthetic example data (`comms_config.TEMPLATE_PREVIEW_EXAMPLE_DATA`), never a real candidate's details.

## Test messaging safeguards (spec section 3/5, explicit)

- Email test-send requires both an explicit `to` recipient **and** `confirm: true` in the request; the subject/body are always prefixed/labeled `[CDPI TEST MESSAGE]` regardless of what the caller passes, so a recipient can never mistake it for a real notification.
- WhatsApp has **no test-message capability at all** (`capabilities.test_message: False`) — Meta's template-approval rules mean there is no safe sandbox send path; only a real connectivity check is offered.
- Both test actions are rate-limited per actor (10s) using a dedicated store — a real bug from the RAG console pass (a shared rate-limit dict silently coupling two unrelated features) was already fixed earlier this session and this new endpoint uses the fixed, parameterized `_rate_limited()`.

## RBAC (6 new permissions)

`comms.config.read`, `comms.config.write`, `comms.config.activate`,
`comms.test.send`, `comms.templates.manage`, `comms.delivery.read` — only
`admin` holds all six by default.

## Tests

58 new tests in `tests/test_comms_config.py`: channel registry integrity
(including the "SMS is absent" assertion), config/interview-bot validation,
`email_adapter.py` exercised against the real legacy-JSON fallback (not
mocked), duplicate-send protection, the WhatsApp notify gating logic
(disabled channel / duplicate / send success / send failure, each
verified to produce a real delivery-log entry), and every new endpoint
(draft/activate/rollback for both lifecycles, template draft/activate/
preview with placeholder validation, test-send requiring confirm+recipient,
RBAC). Full suite: **521 passed** (up from 463).

## Known limitations / unsupported operations (stated, not hidden)

- **SMS is not implemented** — adding it requires real development work: choosing a provider, provisioning an account, and writing a client module, none of which exists yet. This console labels it `absent`, not a fake "coming soon" status.
- **No delivery-status webhooks** — neither Meta nor the SMTP path has an inbound webhook wired up anywhere in this repo, so delivery status never advances past `provider_accepted`/`failed`. The spec's "delivered" status is never fabricated.
- **No fallback-channel logic** — the Delivery Policies tab deliberately only lets an admin pick ONE default channel per workflow. An automatic fallback (e.g., WhatsApp fails → try email) was explicitly NOT built, since doing so without real consent/preference data could violate a candidate's communication preference — the spec's own warning against exactly this.
- **Interview bot has no duration limit, retry policy, or voice settings beyond model/temperature/max_tokens/warm-up-question-count/language** — `voicebot/voice_bot.py` has no code path for any of those today (English-only, no configurable timeout). Listing them would misrepresent what the bot can do.
- **`SendCandidateMetricsEmail.py`'s own `smtplib.SMTP_SSL("smtp.gmail.com", 465)` connection line is untouched** — only its credential source changed. A full migration to `email_adapter.send_email()` for its sending logic (not just credentials) was out of scope for this pass, to avoid rewriting a second already-working script's core logic in the same change.
