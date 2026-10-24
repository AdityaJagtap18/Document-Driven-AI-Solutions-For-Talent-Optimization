# CDPI POC Infrastructure (spin up / tear down)

Everything needed to stand up CDPI's Azure backend for a demo session --
**including Key Vault as the real secret store**, wired up end to end, not
just documented as an option -- and delete it all again afterwards, in one
resource group. Written for a **small, fixed credit balance** (the scripts
assume you want to minimize spend, not maximize capability) — see **Cost**
below before running anything against a real subscription.

*** UNTESTED. *** No Azure CLI/Bicep tooling is available in the
environment this was written in — it's been reviewed against the ARM/
Bicep resource docs, not run end-to-end. Read `main.bicep` yourself before
pointing it at a real subscription, and expect to fix something on your
first real attempt (most likely a region-capacity error on the free-tier
Cognitive Services resources, or an RBAC-propagation-delay 403 on the
first Key Vault secret write — see the gotchas below; both are "re-run
deploy.ps1" fixes, not template bugs).

## What this provisions

| Resource | SKU / tier | Purpose |
|---|---|---|
| Storage Account | Standard_LRS | Blob containers (created lazily by the app, not here) + Function App's own storage |
| Cosmos DB account | Serverless | candidates / interview_transcripts / scoring_history / audit_log (containers created lazily by the app) |
| Document Intelligence | F0 (free) | certificate/marksheet verification |
| Speech | F0 (free) | voicebot TTS/STT |
| Log Analytics + App Insights | PerGB2018 (pay-as-you-go, small free daily grant) | LLM call observability |
| **Key Vault** | Standard, RBAC-authorized | **The real secret store** -- every secret value this template produces (Cosmos key, Blob connection string, Document Intelligence key, Speech key, App Insights connection string) is written directly here, never to `.env` |
| Function App + Plan | Y1 (Consumption, scales to zero) | the HTTP API layer -- reads every secret via Key Vault references + its own system-assigned managed identity, never a plaintext app setting |
| Budget alert | — | optional, see Cost below |

**Note:** `infra/phase2.bicep` is an older, standalone Cosmos+Storage+Key
Vault template that predates this spin-up/down pair and takes resource
names as required params. `main.bicep` below now covers everything it
does (Key Vault included) plus Document Intelligence/Speech/Functions/
App Insights/a budget alert, in one shot, with RBAC role assignments for
both your own `az login` identity and the Function App's managed
identity. Use `main.bicep` + these scripts for the full loop; `phase2.bicep`
is kept only as a narrower, standalone alternative.

**Not provisioned, on purpose:**
- **Azure AI Search** — nothing in the app calls it yet (`semantic-library/`'s `AzureAISearchVectorStore` is written but unused).
- **Any frontend hosting** — the frontend (`frontend/`) and `voicebot/` stay on `localhost`, by design. This template is backend/data services only.
- **WhatsApp Business Cloud API** — that's Meta's platform, not Azure. See `docs/azure-provisioning/phase5-document-intelligence-whatsapp.md`. Set `WHATSAPP_PHONE_NUMBER_ID`/`WHATSAPP_ACCESS_TOKEN` yourself afterward -- either via the app's own Secrets admin page (`/admin/secrets`, writes to `.env`) or `az keyvault secret set --vault-name <name> --name whatsapp-access-token --value <token>` (writes to the Key Vault this script just created).
- **`OPENAI_API_KEY`** — same as WhatsApp: not an Azure resource, set it yourself the same way afterward.

## How Key Vault access actually works here

Two identities get real, least-privilege RBAC roles on the vault (no
access policies, no shared keys):

1. **You** (your own `az login` user) get **Key Vault Secrets Officer**
   (read/write) -- so once `AZURE_KEY_VAULT_URL` is in your local `.env`
   (deploy.ps1 writes it there automatically), `secrets_loader.py`'s
   `DefaultAzureCredential` resolves through your own CLI session with
   zero extra setup, and you can also add further secrets yourself (`az
   keyvault secret set` or the app's own Secrets admin page).
2. **The deployed Function App** gets a **system-assigned managed
   identity** with **Key Vault Secrets User** (read-only) -- its own app
   settings for every secret are Key Vault references
   (`@Microsoft.KeyVault(SecretUri=...)`), resolved by Azure at runtime.
   No secret value is ever stored in the Function App's own
   configuration.

## Gotcha: RBAC propagation delay on first deploy

The role assignment granting you Key Vault access is created in the
*same* deployment that then immediately tries to write secrets into that
vault. Azure RBAC role assignments can take up to a couple of minutes to
actually take effect across the platform, even though the assignment
itself reports success instantly -- so a **first** `deploy.ps1` run can
occasionally fail with a 403/Forbidden on a Key Vault secret write. This
is not a template bug: wait a minute and run `deploy.ps1` again (it's
idempotent -- resources that already exist are left alone, and the still-
missing secrets get created on the retry).

## Prerequisites

1. [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installed. Bicep itself is handled automatically by `az` the first time you deploy.
2. `az login` (and `az account set --subscription <id>` if you have more than one).
3. PowerShell (both scripts are `.ps1` — Windows PowerShell 5.1+ or PowerShell 7+ both work).

## Quickstart

```powershell
cd infra
./deploy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus -AlertEmail you@example.com
```

This creates the resource group (if it doesn't exist), resolves your own signed-in user's object id automatically, deploys everything (including Key Vault), writes every SECRET value directly into Key Vault, and writes only the non-secret endpoints/database/region/`AZURE_KEY_VAULT_URL` into the repo's `.env`. No secret is printed to your terminal or written to `.env` by this script.

When you're done with the session:

```powershell
./destroy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus
```

This deletes the resource group (after a typed confirmation — it's irreversible) and purges the soft-deleted Document Intelligence/Speech accounts and Key Vault left behind, so the *same* `-NamePrefix` can be redeployed cleanly later without a name collision.

Re-running `deploy.ps1` later recreates everything from scratch, with a fresh random suffix if you don't reuse the resource group name — the whole point of this pair of scripts is that nothing has to be hand-recreated from memory.

## ⚠️ Gotcha: you may already have Document Intelligence / Speech resources

Earlier provisioning docs in this repo (`docs/azure-provisioning/phase5-document-intelligence-whatsapp.md`, `docs/azure-provisioning/phase6-speech.md`) walk through creating these **manually in the Portal**. Azure allows only **one F0 (free) resource per kind per region per subscription** — if you already created one that way, this template's attempt to create another F0 one will fail with a quota error.

Either:
- Delete the manually-created ones first and let this template own them, or
- Skip deploying those two resources and keep using the manually-created ones (remove the `documentIntelligence`/`speechService` resources from `main.bicep`, or just don't worry about the failure for those two specific resources and keep the hand-set `.env` values for them — everything else in the template is independent).

## Cost — read this with a $7 balance

Every SKU here was picked to be as close to $0 at idle as Azure allows (serverless Cosmos, Consumption Functions, free-tier Cognitive Services, pay-per-GB Log Analytics with a small daily free grant) — but **none of this is a hard $0 guarantee**, and a `$7` balance disappears fast if something is left running for days or gets real traffic:

- **Cosmos DB serverless**: billed per request (RU) + storage GB/month. A few hours of light POC testing is normally cents, not dollars — but it never fully stops billing storage as long as the account exists.
- **Document Intelligence / Speech F0**: genuinely free up to the monthly page/minute caps (500 pages, 5 hours audio) — $0 unless you blow through those caps, which is hard to do accidentally in a short demo.
- **Functions Consumption**: 1M executions/month free grant — a POC demo won't come close.
- **Log Analytics**: small daily free data grant per workspace; a short POC session's logs are unlikely to exceed it, but it does bill if you leave verbose logging running for a long time.
- **Key Vault**: billed per 10,000 operations (Standard tier) — a POC session's handful of secret reads/writes is a fraction of a cent. The negligible cost in this whole template.

**What the scripts do to help, and what they don't:**
- `deploy.ps1 -AlertEmail` sets up a Cost Management budget alert (80% actual, 100% forecasted) on the resource group — pass your email and this becomes real.
- This is an **alert**, not a spending cap — Azure (outside an actual free-trial subscription type) does not automatically stop resources just because a budget notification fired. Check real spend yourself too: `az consumption usage list --output table`, or the Cost Management blade in the Portal.
- The single biggest lever you have is **time**: `destroy.ps1` the moment you're done with a session rather than leaving it up "just in case." With everything serverless/consumption-based here, an hour of POC testing should be nowhere near $7 — days of it left running idle is the actual risk.
