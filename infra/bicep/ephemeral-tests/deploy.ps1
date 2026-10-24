<#
.SYNOPSIS
    Manual command-line equivalent of what the Azure Readiness dashboard's
    "Provision" action does (azure_readiness.py / ephemeral_test_runner.py)
    -- provisions ONE Standard_LRS storage account, in its OWN fresh
    resource group, for a throwaway Blob Storage connectivity test. Never
    touches any existing resource group.

    *** UNTESTED against a real subscription *** -- this sandbox has no
    Azure CLI installed (confirmed: `az --version` fails with "command not
    found"). Reviewed against the `az`/Bicep CLI docs, not run end-to-end.
    If you run this for real, re-check the az CLI command flags are still
    current for your installed CLI version.

.PARAMETER RunId
    A short unique identifier for this test run (e.g. from
    `[guid]::NewGuid().ToString().Substring(0,8)`). Used to name the
    resource group and tag the storage account, and is the ONLY value
    cleanup.ps1 trusts to know what it's allowed to delete.

.PARAMETER Location
    Azure region. Keep to a region your subscription/tenant actually
    allows (see docs/AZURE_CLI_SETUP.md).

.EXAMPLE
    ./deploy.ps1 -RunId "a1b2c3d4" -Location "eastus"
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]{6,12}$')]
    [string]$RunId,

    [string]$Location = "eastus"
)

$ErrorActionPreference = "Stop"
$ResourceGroupName = "rg-cdpi-ephemeral-$RunId"
$StorageAccountName = "cdpieph$RunId"  # must be globally unique, <=24 chars, lowercase alphanumeric only

Write-Host "=== CDPI Ephemeral Test: Blob Storage ===" -ForegroundColor Cyan
Write-Host "Run ID:          $RunId"
Write-Host "Resource group:  $ResourceGroupName"
Write-Host "Storage account: $StorageAccountName"
Write-Host "Location:        $Location"
Write-Host ""

Write-Host "Step 1/5: Verifying Azure CLI authentication..." -ForegroundColor Yellow
$account = az account show --output json 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Error "Not authenticated. Run 'az login' first. Refusing to provision anything."
    exit 1
}
Write-Host "  Authenticated as $($account.user.name), subscription $($account.name) ($($account.id))."

Write-Host "Step 2/5: Confirming the resource group does not already exist..." -ForegroundColor Yellow
$existing = az group exists --name $ResourceGroupName | ConvertFrom-Json
if ($existing) {
    Write-Error "Resource group '$ResourceGroupName' already exists -- refusing to reuse it (this workflow only ever creates fresh, uniquely-named groups). Pick a different -RunId."
    exit 1
}

Write-Host "Step 3/5: Validating the Bicep template..." -ForegroundColor Yellow
$whatIfOutput = az deployment group what-if `
    --resource-group $ResourceGroupName `
    --template-file "$PSScriptRoot/storage-test.bicep" `
    --parameters storageAccountName=$StorageAccountName location=$Location `
        tags="{project:'CDPI',purpose:'ephemeral-integration-test',testRunId:'$RunId',managedBy:'CDPI-test-runner',createdAt:'$(Get-Date -Format o)'}" `
    2>&1
# what-if against a not-yet-existing resource group will itself fail cleanly
# (nothing to diff against) -- that's expected and informational here, not
# a hard stop; the real validation gate is the template deployment below.
Write-Host $whatIfOutput

Write-Host "Step 4/5: Creating the resource group and deploying..." -ForegroundColor Yellow
az group create --name $ResourceGroupName --location $Location `
    --tags project=CDPI purpose=ephemeral-integration-test testRunId=$RunId managedBy=CDPI-test-runner | Out-Null

az deployment group create `
    --resource-group $ResourceGroupName `
    --template-file "$PSScriptRoot/storage-test.bicep" `
    --parameters storageAccountName=$StorageAccountName location=$Location `
        tags="{project:'CDPI',purpose:'ephemeral-integration-test',testRunId:'$RunId',managedBy:'CDPI-test-runner',createdAt:'$(Get-Date -Format o)'}"

Write-Host "Step 5/5: Done. Run the integration test, then ALWAYS run cleanup.ps1 -RunId $RunId when finished." -ForegroundColor Green
Write-Host "  cleanup.ps1 -RunId $RunId -ResourceGroupName $ResourceGroupName"
