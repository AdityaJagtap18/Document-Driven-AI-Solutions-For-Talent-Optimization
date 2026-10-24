<#
.SYNOPSIS
    Deletes ONLY the ephemeral resource group a specific test run created
    (deploy.ps1 / ephemeral_test_runner.py), verifies deletion actually
    completed (an async delete request is not proof of deletion), and
    reports any resource that survives. Never deletes a resource group
    that doesn't match the exact expected name for -RunId, and never runs
    a subscription-wide or name-prefix-pattern cleanup.

    *** UNTESTED against a real subscription *** -- see deploy.ps1's note.

.PARAMETER RunId
    The same run ID passed to deploy.ps1.

.PARAMETER TimeoutSeconds
    How long to poll for the resource group to actually disappear before
    reporting CLEANUP_FAILED instead of CLEANUP_VERIFIED. Default 300s (5
    min) -- resource group deletion is usually faster, but Cosmos/Storage
    soft-delete-adjacent resources can take longer.

.EXAMPLE
    ./cleanup.ps1 -RunId "a1b2c3d4"
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]{6,12}$')]
    [string]$RunId,

    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$ResourceGroupName = "rg-cdpi-ephemeral-$RunId"

Write-Host "=== CDPI Ephemeral Test Cleanup ===" -ForegroundColor Cyan
Write-Host "Run ID:         $RunId"
Write-Host "Target group:   $ResourceGroupName (exact match only -- this script never touches any other resource group)"
Write-Host ""

$exists = az group exists --name $ResourceGroupName | ConvertFrom-Json
if (-not $exists) {
    Write-Host "Resource group '$ResourceGroupName' does not exist. Nothing to clean up (already deleted, or never deployed)." -ForegroundColor Green
    exit 0
}

# Ownership check: refuse to delete a group that doesn't carry this run's
# own testRunId tag, in case of a name collision -- belt-and-braces on top
# of the exact-name match above.
$tags = az group show --name $ResourceGroupName --query "tags" --output json | ConvertFrom-Json
if ($tags.testRunId -ne $RunId) {
    Write-Error "Resource group '$ResourceGroupName' exists but its testRunId tag ('$($tags.testRunId)') does not match -RunId '$RunId'. Refusing to delete -- this is not a resource this run owns."
    exit 1
}

Write-Host "Initiating deletion (async)..." -ForegroundColor Yellow
az group delete --name $ResourceGroupName --yes --no-wait

Write-Host "Polling for deletion to actually complete (timeout ${TimeoutSeconds}s)..." -ForegroundColor Yellow
$elapsed = 0
$intervalSeconds = 10
while ($elapsed -lt $TimeoutSeconds) {
    Start-Sleep -Seconds $intervalSeconds
    $elapsed += $intervalSeconds
    $stillExists = az group exists --name $ResourceGroupName | ConvertFrom-Json
    if (-not $stillExists) {
        Write-Host "Verified: '$ResourceGroupName' no longer exists. CLEANUP_VERIFIED." -ForegroundColor Green
        exit 0
    }
    Write-Host "  still deleting... (${elapsed}s elapsed)"
}

Write-Host "CLEANUP_FAILED: '$ResourceGroupName' still exists after ${TimeoutSeconds}s." -ForegroundColor Red
Write-Host "Remaining resources:"
az resource list --resource-group $ResourceGroupName --output table
Write-Host ""
Write-Host "Retry: ./cleanup.ps1 -RunId $RunId -TimeoutSeconds <longer>"
exit 1
