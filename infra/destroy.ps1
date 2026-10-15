<#
.SYNOPSIS
    Deletes the entire resource group created by deploy.ps1, then purges
    the soft-deleted Cognitive Services accounts (Document Intelligence,
    Speech) left behind -- without the purge, redeploying later with the
    same NamePrefix can fail because those two resource types reserve
    their name/subdomain for a retention period after deletion. Cosmos DB
    and Storage don't have this issue with the configuration deploy.ps1
    uses, so nothing extra is needed for those.

    *** UNTESTED. *** Same caveat as deploy.ps1 -- reviewed against the
    `az` CLI docs, never run against a real subscription.

.PARAMETER ResourceGroupName
    The resource group to permanently delete. Everything in it goes with
    it -- there is no undo once this finishes.

.PARAMETER Location
    Must match the region deploy.ps1 used -- needed to find the
    soft-deleted Cognitive Services accounts to purge (they're tracked
    per-region, not per-resource-group, since the resource group itself
    won't exist anymore by the time this step runs).

.EXAMPLE
    ./destroy.ps1 -ResourceGroupName cdpi-poc-rg -Location eastus
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = "eastus"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI ('az') not found. Install it first: https://learn.microsoft.com/cli/azure/install-azure-cli"
}

$exists = az group exists --name $ResourceGroupName | ConvertFrom-Json
if (-not $exists) {
    Write-Host "Resource group '$ResourceGroupName' doesn't exist -- nothing to delete. Checking for leftover soft-deleted resources anyway..." -ForegroundColor Yellow
} else {
    Write-Host "This will PERMANENTLY DELETE resource group '$ResourceGroupName' and everything in it." -ForegroundColor Red
    Write-Host "Resources currently in it:" -ForegroundColor Yellow
    az resource list --resource-group $ResourceGroupName --query "[].{Name:name, Type:type}" -o table

    $confirmation = Read-Host "Type the resource group name exactly ('$ResourceGroupName') to confirm deletion"
    if ($confirmation -ne $ResourceGroupName) {
        Write-Host "Confirmation did not match -- aborting. Nothing was deleted." -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Deleting resource group '$ResourceGroupName' (waiting for completion -- this can take several minutes)..." -ForegroundColor Cyan
    az group delete --name $ResourceGroupName --yes
    Write-Host "Resource group deleted." -ForegroundColor Green
}

Write-Host "Purging soft-deleted Cognitive Services accounts in '$Location' (Document Intelligence, Speech) so their names free up for the next deploy..." -ForegroundColor Cyan

$deletedAccounts = az cognitiveservices account list-deleted --query "[?location=='$Location']" -o json | ConvertFrom-Json

foreach ($acct in $deletedAccounts) {
    $acctRg = ($acct.id -split "/")[4]
    $acctName = $acct.name
    Write-Host "Purging '$acctName'..." -ForegroundColor Cyan
    try {
        az cognitiveservices account purge --name $acctName --resource-group $acctRg --location $Location
        Write-Host "  Purged." -ForegroundColor Green
    } catch {
        Write-Host "  Could not purge '$acctName' automatically: $_" -ForegroundColor Yellow
        Write-Host "  If you redeploy with the same prefix and it fails on this resource, purge it manually:" -ForegroundColor Yellow
        Write-Host "    az cognitiveservices account purge --name $acctName --resource-group $acctRg --location $Location" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Teardown complete -- Cosmos DB and everything else in the resource group is gone. The .env file still has the now-invalid endpoints/keys from the last deploy; they'll be overwritten the next time you run deploy.ps1." -ForegroundColor Green
