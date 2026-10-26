# Deploying CDPI to Hostinger (Unlimited Web Hosting / shared plan)

Real constraint this plan works within: Hostinger's shared/Business
plans have no root/SSH shell and no long-running custom process — but
they DO support a managed **Python App** (Phusion Passenger, hPanel ->
Websites -> your site -> Advanced -> **Python App**) and **Cron Jobs**
(hPanel -> Advanced -> Cron Jobs). That's what this folder targets.

This does NOT rewrite the backend. `wsgi_app.py` auto-registers every
real route from `azure-functions/api_blueprint.py` (136 endpoints) by
introspecting the same Blueprint object Azure itself would run — the
actual business logic is byte-for-byte unchanged. See that file's own
docstring for exactly what differs (no Azure function-key gate; CORS
handled explicitly; the Blob trigger becomes a Cron Job).

**Azure Cosmos DB / Blob Storage / Azure OpenAI / Key Vault stay exactly
as they are** — they're just HTTPS APIs; Hostinger only needs real
credentials for them (below), not special network access.

## 1. Create the Python App (backend)

In hPanel: **Websites -> (your site) -> Advanced -> Python App -> Create
Application**.
- Python version: 3.11 or 3.12 (match what this repo was built/tested
  against locally).
- Application root: a new folder, e.g. `cdpi-backend` (NOT your site's
  public document root — Passenger serves it directly, it doesn't need
  to be web-root).
- Application startup file: `passenger_wsgi.py`
- Application URL: a subdomain, e.g. `api.yourdomain.com` (create that
  subdomain first under **Domains**, then point the Python App at it).

## 2. Upload the code

Upload the **whole repository** (not just this folder) into the
Application root, via Git (hPanel's Git integration, if you connect this
repo) or the File Manager / SFTP. `wsgi_app.py` and
`resume_blob_poller.py` both expect the standard layout (`azure-functions/`,
`context-lake/`, the root `.py` modules like `auth.py`, `repositories.py`
via `context-lake/`, etc.) next to them.

## 3. Install dependencies

hPanel's Python App screen has a "Run pip install" box, or SSH isn't
available on shared plans so use that UI field. Point it at:
```
hostinger-deploy/requirements.txt
```
This installs a smaller, deliberately scoped set vs. the repo's root
`requirements.txt` — see that file's own header comment for exactly
what's excluded and why (Selenium/voice-bot extras aren't needed for a
live deployment and some are too heavy for shared hosting).

## 4. Set environment variables

Same Python App screen has an environment-variables section. Set every
one of these the app actually reads (`secrets_registry.py` is the real,
grepped-against-the-codebase source of truth for this list):

**Always required:**
- `JWT_SECRET` -- any long random string, used to sign session tokens
- `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE`
- `BLOB_CONNECTION_STRING`

**Required for AI features (resume scoring, chat, interview questions):**
- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`,
  `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
  (or `OPENAI_API_KEY` if using plain OpenAI instead of Azure OpenAI --
  see `llm_utils.py`)

**Optional (the matching feature degrades gracefully, never crashes, if
omitted):**
- `AZURE_LOG_ANALYTICS_WORKSPACE_ID` (Azure Observability Report's
  Azure-ingested charts)
- `APPLICATIONINSIGHTS_CONNECTION_STRING`
- `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`
- `SMTP_EMAIL`, `SMTP_PASSWORD`, `SMTP_SERVER`, `SMTP_PORT`
- `DOCUMENT_INTELLIGENCE_ENDPOINT`, `DOCUMENT_INTELLIGENCE_KEY`
- `AZURE_KEY_VAULT_URL` (only if you want secrets pulled from Key Vault
  instead of these env vars directly -- most deployments can skip this
  and just set the values above directly)

**New for this deployment path:**
- `CDPI_CORS_ALLOWED_ORIGIN` -- set to your real frontend origin, e.g.
  `https://app.yourdomain.com` (defaults to `*` if unset, which works
  but is looser than necessary once you know the real frontend URL)

## 5. Start/restart the app

The Python App screen has a Restart button -- use it after any env var
or dependency change. Then confirm:
```
curl https://api.yourdomain.com/api/health
# -> {"status": "ok"}
```

## 6. Replace the Blob trigger with a Cron Job

hPanel -> **Advanced -> Cron Jobs -> Create a new Cron Job**:
- Command: `/home/<user>/<app-dir>/venv/bin/python /home/<user>/<app-dir>/hostinger-deploy/resume_blob_poller.py`
  (the exact venv path is shown on the Python App screen itself)
- Schedule: every 5 minutes is a reasonable default (`*/5 * * * *`) --
  resumes won't appear instantly like the real Azure Blob trigger does,
  a real and stated trade-off of shared hosting having no event
  mechanism.

Run it once manually first (via the Python App's "Execute a command"
field, or just hit it with the same command over SSH if your plan has
one) to confirm it finds and processes a real test resume before
scheduling it.

## 7. Deploy the frontend

The frontend has no build step (see `frontend/README.md`) -- it's
already static files. Upload the entire `frontend/` folder's contents
(not the folder itself) into your site's `public_html` (or a
subdomain's document root if you want frontend and backend on separate
subdomains, e.g. `app.yourdomain.com` for the frontend,
`api.yourdomain.com` for the backend from step 1).

Then edit **`frontend/src/config.js`** (the copy you upload) before
uploading it, or after via File Manager:
```js
window.CDPI_CONFIG = {
  mode: "real",
  baseUrl: "https://api.yourdomain.com",   // <- your real backend subdomain from step 1
  functionKey: "",                          // leave empty -- not enforced on this deployment, see wsgi_app.py
};
```

## 8. Verify end to end

1. Open the frontend URL, log in as one of the real accounts (see
   `private/CDPI_Persona_Credentials.xlsx` -- not committed, your own
   copy).
2. Submit a test application as a candidate.
3. Run `resume_blob_poller.py` manually (or wait for its Cron schedule)
   and confirm the candidate shows up scored in the Review Queue.
4. Check the backend's error log (Python App screen has a log viewer)
   for anything unexpected.

## Known limitations of this deployment path (real, not hidden)

- No Azure Functions platform-level function-key auth -- the real
  authorization boundary (JWT + `require_permission()` in
  `api_blueprint.py`) is unchanged and still fully enforced; this only
  removes an Azure-platform-specific extra layer that was already a
  no-op during every local `func start` run.
- Resume processing is on a poll interval (Cron Job), not instant
  (real Blob trigger event) -- see step 6.
- Voice interview bot (`interview_bot.py`) and the Selenium E2E suite
  aren't installed on this path (see `requirements.txt`'s header) --
  install those extras yourself if you need them, ideally on a VPS
  plan given their resource footprint (`openai-whisper` pulls in torch).
- Shared hosting has no long-lived background workers -- anything that
  used to run continuously (the old `file_watcher.py` polling loop) must
  go through Cron Jobs like step 6, not stay running in-process.
