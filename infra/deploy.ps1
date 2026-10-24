<#
.SYNOPSIS
    Stands up every Azure resource CDPI's code currently needs (Cosmos DB,
    Blob Storage, Document Intelligence, Speech, Functions + App Insights,
    Key Vault), all in one resource group -- and wires every SECRET value
    into Key Vault (never into .env), granting you (via your own `az login`
    identity) and the deployed Function App (via its system-assigned
    managed identity) real, least-privilege read/write access to it. Only
    non-secret values (endpoints, database name, region, Key Vault's own
    URL) plus AZURE_KEY_VAULT_URL get written to the repo's .env --
    secrets_loader.py (already in this repo) then resolves every secret
    from Key Vault automatically, with zero code changes needed. Pair with
    destroy.ps1 to tear it all down again when you're done with a session.

    *** UNTESTED. *** Written without Azure CLI available to run it against
    a real subscription -- reviewed against the `az`/Bicep docs, not
    verified end-to-end. Expect to debug something on your first real run.

.PARAMETER ResourceGroupName
    Everything gets created inside this resource group. Pick a name you
    won't mind typing into destroy.ps1 later.

.PARAMETER Location
    Azure region. F0 (free-tier) Document Intelligence and Speech are only
    available in some regions/subscriptions (one F0 resource per kind per
    subscription) -- if deployment fails with a quota/capacity error on
    those two, it's almost always this.

.PARAMETER NamePrefix
    Short prefix for every resource name (lowercase letters/numbers, keep
    it under ~12 chars -- Storage Account names have a strict 24-char
    total limit and this gets a random suffix appended).

.PARAMETER AlertEmail
    Email for a budget alert (80% actual, 100% forecasted). Strongly
    recommended if you're running on a small, fixed credit balance -- see
    README.md's cost section. Omit to skip the budget alert.

.PARAMETER BudgetAmountUsd
    Monthly budget cap in USD that triggers the alert above. Default (5)
    leaves a margin under a $7 balance -- lower it if yours is smaller.
    This is an alert, not a hard spending cap; check actual spend
    yourself too (README.md).

.PARAMETER DeployerPrincipalId
    Azure AD object id to grant "Key Vault Secrets Officer" on the new
    vault. Defaults to your own signed-in `az login` user (resolved
    automatically) -- only pass this explicitly if you're deploying under
    a service principal instead of a real user account.

.PARAMETER SkipBootstrap
    Skip running context-lake/bootstrap_iam.py after the Azure deploy
    finishes. By default this script runs it for you -- it seeds the
    default IAM roles, the page-access registry, the Azure Service Hub's
    default integration instances, the RAG prompt versions, and the
    Communications templates, all from the now-real Cosmos DB this script
    just provisioned, so every admin console in the app reflects real
    data immediately instead of needing a separate manual step. Requires
    Python + this repo's requirements.txt already installed locally; if
    that's not set up yet, pass this switch and run it yourself later.

.EXAMPLE
    ./deploy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus -AlertEmail you@example.com -BudgetAmountUsd 5
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = "eastus",

    [string]$NamePrefix = "cdpipoc",

    [string]$AlertEmail = "",

    [int]$BudgetAmountUsd = 5,

    [string]$DeployerPrincipalId = "",

    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"

function Require-AzCli {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI ('az') not found. Install it first: https://learn.microsoft.com/cli/azure/install-azure-cli"
    }
    $account = az account show --query "user.name" -o tsv 2>$null
    if (-not $account) {
        throw "Not logged in to Azure CLI. Run 'az login' first."
    }
    Write-Host "Logged in as: $account" -ForegroundColor Cyan
}

function Get-DeployerPrincipalId {
    # The signed-in USER's Azure AD object id -- this is who gets "Key
    # Vault Secrets Officer" on the vault this template creates, so your
    # own `az login` session can read/write secrets afterward. Only works
    # for a real user account (not a service principal) -- if you're
    # running this under a service principal, pass its object id via
    # -DeployerPrincipalId instead and this lookup is skipped.
    $id = az ad signed-in-user show --query id -o tsv 2>$null
    if (-not $id) {
        throw "Could not resolve your signed-in user's object id (az ad signed-in-user show failed -- this is expected if you're logged in as a service principal, not a user). Pass -DeployerPrincipalId <object-id> explicitly."
    }
    return $id
}

function Ensure-ResourceGroup {
    param([string]$Name, [string]$Loc)
    $exists = az group exists --name $Name | ConvertFrom-Json
    if (-not $exists) {
        Write-Host "Creating resource group '$Name' in '$Loc'..." -ForegroundColor Cyan
        az group create --name $Name --location $Loc --output none
    } else {
        Write-Host "Resource group '$Name' already exists -- reusing it." -ForegroundColor Yellow
    }
}

function Update-DotEnv {
    param([hashtable]$Values, [string]$EnvPath)

    if (-not (Test-Path $EnvPath)) {
        Write-Host "No .env found at $EnvPath -- creating a new one with just the Azure values." -ForegroundColor Yellow
        $lines = @()
    } else {
        $lines = Get-Content $EnvPath
    }

    foreach ($key in $Values.Keys) {
        $newLine = "$key=$($Values[$key])"
        $pattern = "^$key="
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match $pattern) {
                $lines[$i] = $newLine
                $found = $true
                break
            }
        }
        if (-not $found) {
            $lines += $newLine
        }
    }

    Set-Content -Path $EnvPath -Value $lines -Encoding utf8
    Write-Host "Wrote $($Values.Count) value(s) to $EnvPath" -ForegroundColor Green
}

Require-AzCli
Ensure-ResourceGroup -Name $ResourceGroupName -Loc $Location

if (-not $DeployerPrincipalId) {
    Write-Host "Resolving your signed-in user's object id (for Key Vault access)..." -ForegroundColor Cyan
    $DeployerPrincipalId = Get-DeployerPrincipalId
}

$bicepFile = Join-Path $PSScriptRoot "main.bicep"
$deploymentName = "cdpi-poc-$(Get-Date -Format 'yyyyMMddHHmmss')"

Write-Host "Deploying $bicepFile to '$ResourceGroupName' (this can take 5-10 minutes, mostly Cosmos DB + Function App)..." -ForegroundColor Cyan

if (-not $AlertEmail) {
    Write-Host "No -AlertEmail given: deploying WITHOUT a budget alert. On a small/fixed credit balance, re-run with -AlertEmail to get an 80%/forecasted-100% warning." -ForegroundColor Yellow
}

az deployment group create `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --template-file $bicepFile `
    --parameters namePrefix=$NamePrefix location=$Location alertEmail=$AlertEmail budgetAmountUsd=$BudgetAmountUsd deployerPrincipalId=$DeployerPrincipalId `
    --output none

Write-Host "Deployment finished. Fetching outputs..." -ForegroundColor Cyan

$outputsJson = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --query "properties.outputs" `
    -o json

$outputs = $outputsJson | ConvertFrom-Json

# NON-SECRET values only -- every secret the template produced (Cosmos
# key, Blob connection string, Document Intelligence key, Speech key,
# App Insights connection string) already landed directly in Key Vault
# (see main.bicep) and was never an `output` of this deployment at all.
# AZURE_KEY_VAULT_URL is the one new line that makes secrets_loader.py
# start resolving everything else from there -- no secret value is
# written to .env by this script.
$envValues = @{
    "COSMOS_ENDPOINT"                 = $outputs.cosmosEndpoint.value
    "COSMOS_DATABASE"                 = $outputs.cosmosDatabase.value
    "DOCUMENT_INTELLIGENCE_ENDPOINT"  = $outputs.documentIntelligenceEndpoint.value
    "AZURE_SPEECH_REGION"             = $outputs.speechRegion.value
    "AZURE_KEY_VAULT_URL"             = $outputs.keyVaultUri.value
}

$repoRoot = Split-Path $PSScriptRoot -Parent
Update-DotEnv -Values $envValues -EnvPath (Join-Path $repoRoot ".env")

if (-not $SkipBootstrap) {
    Write-Host ""
    Write-Host "Running context-lake/bootstrap_iam.py against the real Cosmos DB this just provisioned (seeds roles, page access, Azure/RAG/Communications defaults)..." -ForegroundColor Cyan
    try {
        Push-Location (Join-Path $repoRoot "context-lake")
        python bootstrap_iam.py
        Pop-Location
    } catch {
        Pop-Location -ErrorAction SilentlyContinue
        Write-Host "bootstrap_iam.py failed (often just a missing/incomplete Python environment) -- Azure resources are still fully provisioned. Fix your environment and run it yourself:" -ForegroundColor Yellow
        Write-Host "  cd context-lake; python bootstrap_iam.py" -ForegroundColor Yellow
        Write-Host "Error: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "Skipped bootstrap_iam.py (-SkipBootstrap) -- run it yourself when ready: cd context-lake; python bootstrap_iam.py" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Resource group: $ResourceGroupName  |  Function App: $($outputs.functionAppName.value)  |  Key Vault: $($outputs.keyVaultUri.value)" -ForegroundColor Green
Write-Host "Frontend/voicebot/CLI scripts still run on localhost -- only the backend services above are in Azure." -ForegroundColor Green
Write-Host ""
Write-Host "Every secret (Cosmos key, Blob connection string, Document Intelligence key, Speech key, App Insights connection string) is in Key Vault now, not .env -- your az login identity ($DeployerPrincipalId) has 'Key Vault Secrets Officer' on it, and the deployed Function App reads them via its own managed identity. secrets_loader.py picks everything up automatically from here, no further code changes needed." -ForegroundColor Green
Write-Host ""
Write-Host "WhatsApp (Meta, not Azure) and OPENAI_API_KEY are untouched by this script -- set those via the Secrets admin page (/admin/secrets) or add them to Key Vault yourself: az keyvault secret set --vault-name <name> --name openai-api-key --value <key>" -ForegroundColor Yellow
Write-Host ""
Write-Host "If this is your FIRST deploy and a Key Vault secret write failed with a 403/Forbidden, that's RBAC propagation delay (the role assignment this script just created hasn't taken effect across Azure yet) -- wait a minute and re-run deploy.ps1; it's idempotent." -ForegroundColor Yellow
Write-Host ""
Write-Host "COST: with ~`$7 in credits, don't leave this running idle for days -- Cosmos DB serverless/Functions/Document Intelligence/Speech bill per-use with free grants, but nothing here is a hard `$0 guarantee. Tear it down the moment you're done:" -ForegroundColor Red
Write-Host "  ./destroy.ps1 -ResourceGroupName $ResourceGroupName -Location $Location" -ForegroundColor Yellow
Write-Host "Check actual spend any time: az consumption usage list --output table   (or the Cost Management blade in the Portal)" -ForegroundColor Yellow
