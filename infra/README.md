# CDPI POC Infrastructure (spin up / tear down)

Everything needed to stand up CDPI's Azure backend for a demo session, and
delete it all again afterwards, in one resource group. Written for a
**small, fixed credit balance** (the scripts assume you want to minimize
spend, not maximize capability) — see **Cost** below before running
anything against a real subscription.

*** UNTESTED. *** No Azure CLI/Bicep tooling is available in the
environment this was written in — it's been reviewed against the ARM/
Bicep resource docs, not run end-to-end. Read `main.bicep` yourself before
pointing it at a real subscription, and expect to fix something on your
first real attempt (most likely a region-capacity error on the free-tier
Cognitive Services resources — see the gotcha below).

## What this provisions

| Resource | SKU / tier | Purpose |
|---|---|---|
| Storage Account | Standard_LRS | Blob containers (created lazily by the app, not here) + Function App's own storage |
| Cosmos DB account | Serverless | candidates / interview_transcripts / scoring_history / audit_log (containers created lazily by the app) |
| Document Intelligence | F0 (free) | certificate/marksheet verification |
| Speech | F0 (free) | voicebot TTS/STT |
| Log Analytics + App Insights | PerGB2018 (pay-as-you-go, small free daily grant) | LLM call observability |
| Function App + Plan | Y1 (Consumption, scales to zero) | the HTTP API layer |
| Budget alert | — | optional, see Cost below |

**Note:** `infra/phase2.bicep` already existed in this repo from Phase 2 (Cosmos + Storage + Key Vault, with RBAC, no free-tier Cognitive Services or Functions) — it predates this spin-up/down pair and takes resource names as required params rather than generating them. `main.bicep` below covers everything it does except Key Vault, plus Document Intelligence/Speech/Functions/App Insights/a budget alert, in one shot. Use `main.bicep` + these scripts for the full spin-up/down POC loop; keep `phase2.bicep` around only if you specifically want Key Vault+RBAC without the rest.

**Not provisioned, on purpose:**
- **Key Vault** — unnecessary for a throwaway POC loop; `.env` already works (that's the code's fallback today). Use `phase2.bicep` instead if you want it.
- **Azure AI Search** — nothing in the app calls it yet (`semantic-library/`'s `AzureAISearchVectorStore` is written but unused).
- **Any frontend hosting** — the frontend (`frontend/`) and `voicebot/` stay on `localhost`, by design. This template is backend/data services only.
- **WhatsApp Business Cloud API** — that's Meta's platform, not Azure. See `docs/azure-provisioning/phase5-document-intelligence-whatsapp.md`.

## Prerequisites

1. [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installed. Bicep itself is handled automatically by `az` the first time you deploy.
2. `az login` (and `az account set --subscription <id>` if you have more than one).
3. PowerShell (both scripts are `.ps1` — Windows PowerShell 5.1+ or PowerShell 7+ both work).

## Quickstart

```powershell
cd infra
./deploy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus -AlertEmail you@example.com
```

This creates the resource group (if it doesn't exist), deploys everything, and writes the resulting endpoints/keys straight into the repo's `.env`. Nothing is printed to your terminal in plaintext by the script itself.

When you're done with the session:

```powershell
./destroy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus
```

This deletes the resource group (after a typed confirmation — it's irreversible) and purges the soft-deleted Document Intelligence/Speech accounts left behind, so the *same* `-NamePrefix` can be redeployed cleanly later without a name collision.

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

**What the scripts do to help, and what they don't:**
- `deploy.ps1 -AlertEmail` sets up a Cost Management budget alert (80% actual, 100% forecasted) on the resource group — pass your email and this becomes real.
- This is an **alert**, not a spending cap — Azure (outside an actual free-trial subscription type) does not automatically stop resources just because a budget notification fired. Check real spend yourself too: `az consumption usage list --output table`, or the Cost Management blade in the Portal.
- The single biggest lever you have is **time**: `destroy.ps1` the moment you're done with a session rather than leaving it up "just in case." With everything serverless/consumption-based here, an hour of POC testing should be nowhere near $7 — days of it left running idle is the actual risk.
