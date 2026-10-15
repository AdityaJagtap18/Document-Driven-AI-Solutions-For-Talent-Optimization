# Phase 5 Provisioning: Document Intelligence + WhatsApp Business Cloud API

Two unrelated services, both needed for Phase 5. Document Intelligence is an Azure resource (Portal, ~5 minutes). WhatsApp is a Meta (Facebook) developer setup, not Azure at all (~20-30 minutes, including waiting for template approval).

## 1. Azure AI Document Intelligence

Portal → "Document Intelligence" (previously "Form Recognizer") → Create.
- Resource group: `cdpi-rg` (same one from Phase 2, if you made one)
- Region: same as your other resources
- Pricing tier: **Free (F0)** if available in your region — covers 500 pages/month, plenty for development. Otherwise Standard (S0), pay-per-page.
- Create.

Once created: resource → **Keys and Endpoint** (left sidebar) → copy:
- `Endpoint` → this is `DOCUMENT_INTELLIGENCE_ENDPOINT`
- `KEY 1` → this is `DOCUMENT_INTELLIGENCE_KEY`

Add both to `.env`:
```env
DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com/
DOCUMENT_INTELLIGENCE_KEY=your-key-here
```

**Model note**: `document_intelligence_client.py` defaults to the generic `prebuilt-document` model (works out of the box, general key-value extraction, lower accuracy on this project's specific certificate/marksheet layouts). A **custom-trained model** would extract far more reliably once you have ~5+ sample certificates/marksheets to train on — set that up later via [Document Intelligence Studio](https://documentintelligence.ai.azure.com/) (no code changes needed here beyond passing a different `model_id` to `analyze_document()`).

## 2. WhatsApp Business Cloud API (Meta)

This is **not an Azure resource** — it's Meta's own developer platform. Steps:

1. **Create a Meta Developer account** (if you don't have one): [developers.facebook.com](https://developers.facebook.com) → sign up with a Facebook account.
2. **Create a Meta App**: My Apps → Create App → type **"Business"** → name it (e.g. "CDPI Notifications").
3. **Add the WhatsApp product**: in the app dashboard → Add Product → WhatsApp → Set Up.
4. Meta gives you a **test phone number** automatically (free, limited to 5 recipient numbers you manually verify — fine for development). For production, you'd add and verify your own business phone number instead (Settings → Phone Numbers).
5. From the WhatsApp → API Setup page, copy:
   - **Phone number ID** → this is `WHATSAPP_PHONE_NUMBER_ID`
   - **Temporary access token** (valid 24h, for testing) — for anything beyond quick testing, generate a **permanent token** instead: Business Settings → System Users → create a system user → assign it the WhatsApp app → generate a token with `whatsapp_business_messaging` permission, no expiry. This is `WHATSAPP_ACCESS_TOKEN`.

Add both to `.env`:
```env
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_ACCESS_TOKEN=your-permanent-access-token
```

6. **Create and get a message template approved**: `whatsapp_client.py`'s `send_whatsapp_template()` (the function actually wired into the review endpoint) requires a pre-approved template — Meta does not allow arbitrary free-text outbound messages outside an active 24-hour conversation window, which covers essentially every notification this project sends (interview invites, status updates). In the Meta App dashboard: WhatsApp → Message Templates → Create Template.
   - Name it `application_status_update` (matches what `api_blueprint.py`'s `_notify_candidate_whatsapp()` already calls) — or create your own name and change that one string in `api_blueprint.py`.
   - Category: "Utility" (status/transactional, not marketing — approves faster and isn't subject to marketing-template restrictions).
   - Body text with placeholders, e.g.: `Hi {{1}}, your application status is now: {{2}}.`
   - Submit for review — Meta typically approves utility templates within a few hours to a day.

**Until the template is approved**, `send_whatsapp_template()` calls will fail with a clear Meta API error (caught by `_notify_candidate_whatsapp()`'s fail-soft wrapper — it won't break the review endpoint, just won't actually send).

## Verifying it works

Once both are configured:
- **Document Intelligence**: `POST /api/candidates/{id}/verify-document` with a real base64-encoded PDF/image and some `claimed_fields` — should return real extracted field values instead of a 503.
- **WhatsApp**: `POST /api/candidates/{id}/review` on a candidate with a real `Phone` number (format: country code + number, no `+` or spaces, e.g. `15551234567`) — the response's `whatsapp_notification` field should say `"sent"` instead of a `"failed: ..."` message, and the test phone number you verified in step 4 above should receive the message.

Neither of these has been exercised against the real services in this session — they're the actual verification this component still needs once provisioned.
