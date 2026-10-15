<#
.SYNOPSIS
    Stands up every Azure resource CDPI's code currently needs (Cosmos DB,
    Blob Storage, Document Intelligence, Speech, Functions + App Insights),
    all in one resource group, and writes the resulting endpoints/keys into
    the repo's .env file. Pair with destroy.ps1 to tear it all down again
    when you're done with a POC session.

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

.EXAMPLE
    ./deploy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus -AlertEmail you@example.com -BudgetAmountUsd 5
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = "eastus",

    [string]$NamePrefix = "cdpipoc",

    [string]$AlertEmail = "",

    [int]$BudgetAmountUsd = 5
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
    --parameters namePrefix=$NamePrefix location=$Location alertEmail=$AlertEmail budgetAmountUsd=$BudgetAmountUsd `
    --output none

Write-Host "Deployment finished. Fetching outputs..." -ForegroundColor Cyan

$outputsJson = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --query "properties.outputs" `
    -o json

$outputs = $outputsJson | ConvertFrom-Json

$envValues = @{
    "COSMOS_ENDPOINT"                        = $outputs.cosmosEndpoint.value
    "COSMOS_KEY"                              = $outputs.cosmosKey.value
    "COSMOS_DATABASE"                         = $outputs.cosmosDatabase.value
    "BLOB_CONNECTION_STRING"                  = $outputs.blobConnectionString.value
    "DOCUMENT_INTELLIGENCE_ENDPOINT"          = $outputs.documentIntelligenceEndpoint.value
    "DOCUMENT_INTELLIGENCE_KEY"               = $outputs.documentIntelligenceKey.value
    "AZURE_SPEECH_KEY"                        = $outputs.speechKey.value
    "AZURE_SPEECH_REGION"                     = $outputs.speechRegion.value
    "APPLICATIONINSIGHTS_CONNECTION_STRING"   = $outputs.appInsightsConnectionString.value
}

$repoRoot = Split-Path $PSScriptRoot -Parent
Update-DotEnv -Values $envValues -EnvPath (Join-Path $repoRoot ".env")

Write-Host ""
Write-Host "Done. Resource group: $ResourceGroupName  |  Function App: $($outputs.functionAppName.value)" -ForegroundColor Green
Write-Host "Frontend/voicebot/CLI scripts still run on localhost -- only the backend services above are in Azure." -ForegroundColor Green
Write-Host ""
Write-Host "WhatsApp (Meta, not Azure) and OPENAI_API_KEY are untouched by this script -- add those to .env yourself if needed." -ForegroundColor Yellow
Write-Host ""
Write-Host "COST: with ~`$7 in credits, don't leave this running idle for days -- Cosmos DB serverless/Functions/Document Intelligence/Speech bill per-use with free grants, but nothing here is a hard `$0 guarantee. Tear it down the moment you're done:" -ForegroundColor Red
Write-Host "  ./destroy.ps1 -ResourceGroupName $ResourceGroupName -Location $Location" -ForegroundColor Yellow
Write-Host "Check actual spend any time: az consumption usage list --output table   (or the Cost Management blade in the Portal)" -ForegroundColor Yellow
