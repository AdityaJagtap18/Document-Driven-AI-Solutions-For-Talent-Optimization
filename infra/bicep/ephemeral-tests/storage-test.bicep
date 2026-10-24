// Ephemeral Blob Storage integration test resource -- the ONLY resource
// type this ephemeral-testing workflow provisions today (see
// ephemeral_test_runner.py's ALLOWED_RESOURCE_PLANS -- scope is
// deliberately limited to one cheap, safe type, not every Azure service
// mentioned in the project docs).
//
// Cheapest safe SKU (Standard_LRS, StorageV2, no premium tier, no
// always-on compute) -- cost is a few cents per day if cleanup is
// delayed, effectively free for a test that runs and is torn down within
// minutes. Deployed ONLY into a dedicated, freshly created resource
// group (see ephemeral_test_runner.py) -- never into an existing/shared
// resource group, so deletion can target the whole group with no risk
// to unrelated resources.
targetScope = 'resourceGroup'

@description('Globally unique storage account name (max 24 chars, lowercase alphanumeric only).')
@maxLength(24)
param storageAccountName string

@description('Azure region for the test resource.')
param location string = resourceGroup().location

@description('Tags identifying this as an ephemeral CDPI integration test resource, including the test-run ID that owns it -- used by cleanup to verify ownership before deleting anything.')
param tags object

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    accessTier: 'Hot'
  }
}

// Non-secret outputs only -- the connection string/keys are fetched by
// the test runner via `az storage account keys list`, scoped to this
// one run, and never persisted or logged (see ephemeral_test_runner.py).
output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id
