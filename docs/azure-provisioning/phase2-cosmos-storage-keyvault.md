# Phase 2 Provisioning: Cosmos DB + Storage + Key Vault

**There is now a fully automated path that provisions this (and Document
Intelligence, Speech, Application Insights, and a Function App) in one
command, with Key Vault wired up end to end via RBAC and managed
identity: `infra/deploy.ps1` (see `infra/README.md`).** The manual steps
below still work and remain useful if you want just these 3 resources,
want to understand exactly what gets created, or don't have the Azure
CLI installed.

You need to create 3 Azure resources before the code changes in this phase can actually connect to anything real. Two paths below — pick one. Both produce the same result.

## Option A: Azure Portal (click-through, ~15 minutes)

### 1. Resource Group (if you don't already have one for this project)
Portal → "Resource groups" → Create → name it e.g. `cdpi-rg`, pick a region close to you (e.g. `Central India` or `East US`).

### 2. Azure Cosmos DB account
Portal → "Azure Cosmos DB" → Create → **Azure Cosmos DB for NoSQL**.
- Resource group: `cdpi-rg`
- Account name: e.g. `cdpi-cosmos` (must be globally unique)
- **Capacity mode: Serverless** — pay-per-request, no fixed monthly cost, the right choice at this project's current scale (dozens to low-hundreds of candidates). Switch to provisioned throughput later only if volume grows a lot.
- Region: same as the resource group
- Leave networking/backup on defaults for now
- Create. Takes a few minutes to deploy.

Once created: Cosmos DB account → **Keys** (left sidebar) → copy:
- `URI` → this is `COSMOS_ENDPOINT`
- `PRIMARY KEY` → this is `COSMOS_KEY`

Database/containers don't need to be created manually — `context-lake/client.py` creates them automatically on first use (`create_database_if_not_exists` / `create_container_if_not_exists`).

### 3. Azure Storage account
Portal → "Storage accounts" → Create.
- Resource group: `cdpi-rg`
- Storage account name: e.g. `cdpistorage` (globally unique, lowercase/numbers only)
- Performance: Standard. Redundancy: **LRS** is fine to start (cheapest); switch to GRS later if you want geo-redundancy for resume PDFs.
- Create.

Once created: Storage account → **Access keys** (left sidebar) → **Connection string** → copy it. This is `BLOB_CONNECTION_STRING`.

Containers (`raw-resumes`, `parsed-resumes`) are auto-created by `context-lake/blob_store.py` on first use.

### 4. Azure Key Vault
Portal → "Key vaults" → Create.
- Resource group: `cdpi-rg`
- Key vault name: e.g. `cdpi-keyvault` (globally unique)
- Pricing tier: Standard
- Create.

Once created: Key Vault → **Access control (IAM)** → Add role assignment → role **"Key Vault Secrets Officer"** (to add/edit secrets) and separately **"Key Vault Secrets User"** (for the app/your account to read them) → assign to your own Azure AD account (for now, since you'll be running scripts locally under your own `az login` identity).

Then: Key Vault → **Secrets** → **Generate/Import** → add these one at a time (names must use hyphens, not underscores — Key Vault doesn't allow underscores):

| Secret name in Key Vault | Value |
|---|---|
| `openai-api-key` | your OpenAI key |
| `gmail-watcher-app-password` | the Gmail app password |
| (any other secret you want centralized later) | |

The Key Vault's own URL (`https://cdpi-keyvault.vault.azure.net/`) is **not** a secret — it goes in `.env` as `AZURE_KEY_VAULT_URL` in plain text. Access is controlled by Azure AD identity (via `az login` locally, or Managed Identity once this runs on an actual Azure compute resource), not a key you copy around.

## Option B: Bicep (one command, if you have the Azure CLI installed)

Save this as `infra/phase2.bicep` (already included in this repo at that path) and run:

```bash
az login
az group create --name cdpi-rg --location centralindia
az deployment group create --resource-group cdpi-rg --template-file infra/phase2.bicep --parameters cosmosAccountName=cdpi-cosmos storageAccountName=cdpistorage keyVaultName=cdpi-keyvault
```

(Account/storage/vault names must be globally unique — change the parameter values if the deployment reports a name collision.)

After it finishes, get the connection details:
```bash
az cosmosdb show --name cdpi-cosmos --resource-group cdpi-rg --query documentEndpoint -o tsv
az cosmosdb keys list --name cdpi-cosmos --resource-group cdpi-rg --query primaryMasterKey -o tsv
az storage account show-connection-string --name cdpistorage --resource-group cdpi-rg -o tsv
```

Then grant yourself access to the Key Vault (Bicep creates it with no access policies by default):
```bash
az role assignment create --role "Key Vault Secrets Officer" --assignee <your-azure-ad-email> --scope $(az keyvault show --name cdpi-keyvault --resource-group cdpi-rg --query id -o tsv)
```

## After either option: fill in `.env`

```env
COSMOS_ENDPOINT=https://cdpi-cosmos.documents.azure.com:443/
COSMOS_KEY=<primary key from step 2>
COSMOS_DATABASE=cdpi
BLOB_CONNECTION_STRING=<connection string from step 3>
AZURE_KEY_VAULT_URL=https://cdpi-keyvault.vault.azure.net/
```

Once `AZURE_KEY_VAULT_URL` is set, `secrets_loader.py` (see `docs/architecture.md` / the Phase 2 commit) automatically starts reading `OPENAI_API_KEY` and `GMAIL_WATCHER_APP_PASSWORD` from Key Vault instead of `.env` — no code changes needed, and nothing breaks if you leave it unset (falls back to `.env` exactly as before).

## What to hand back

Once provisioned, you don't need to paste raw keys into chat — just confirm "Cosmos DB, Storage, and Key Vault are provisioned, .env is filled in" and the next session can run `context-lake/migrate_from_csv.py` for real (dry-run already verified: 84 candidates, 85 interview transcripts) and verify the dual-write path actually lands data in Cosmos DB.
