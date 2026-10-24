// Real Azure services for the CDPI Azure real-services audit --
// provisions Key Vault, Azure OpenAI (chat + embedding deployments), and
// Application Insights (workspace-based) into an EXISTING resource group
// (never creates a new one -- this subscription's account only has
// Owner at the one pre-existing resource group, confirmed via
// `az role assignment list`, not subscription-wide create rights).
//
// Deployed with explicit human approval, see
// docs/AZURE_REAL_SERVICES_AUDIT.md Section 0 for the approved plan and
// cost estimate. These resources are meant to be RETAINED (not torn
// down after testing, unlike infra/bicep/ephemeral-tests/) -- real,
// ongoing (near-zero-cost-at-this-usage-level) application
// configuration, not a throwaway connectivity probe.
targetScope = 'resourceGroup'

@description('Azure region for all three resources.')
param location string = 'eastus'

@description('Object ID of the principal to grant Key Vault Secrets Officer on the new vault.')
param deployerPrincipalId string

@description('Unique suffix for resource names (keeps names valid/unique without hardcoding one).')
param uniqueSuffix string = uniqueString(resourceGroup().id, deployment().name)

var keyVaultName = toLower('kv-cdpi-${substring(uniqueSuffix, 0, 8)}')
var openAiName = toLower('aoi-cdpi-${substring(uniqueSuffix, 0, 8)}')
var logAnalyticsName = 'law-cdpi-${substring(uniqueSuffix, 0, 8)}'
var appInsightsName = 'appi-cdpi-${substring(uniqueSuffix, 0, 8)}'
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: { project: 'CDPI', purpose: 'real-services-verification' }
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerPrincipalId, keyVaultSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: openAiName
  location: location
  tags: { project: 'CDPI', purpose: 'real-services-verification' }
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAiAccount
  name: 'gpt-4o-mini'
  sku: { name: 'Standard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4o-mini', version: '2024-07-18' }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAiAccount
  name: 'text-embedding-3-small'
  sku: { name: 'Standard', capacity: 120 }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' }
  }
  dependsOn: [
    chatDeployment
  ]
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  tags: { project: 'CDPI', purpose: 'real-services-verification' }
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: { project: 'CDPI', purpose: 'real-services-verification' }
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

// Non-secret outputs only -- API keys / connection strings are fetched
// separately via `az ... list-keys` / `show --query connectionString`
// and written directly to .env (never printed), same discipline as
// infra/deploy.ps1 already uses for Cosmos/Storage.
output keyVaultName string = keyVaultName
output keyVaultUri string = keyVault.properties.vaultUri
output openAiName string = openAiName
output openAiEndpoint string = openAiAccount.properties.endpoint
output appInsightsName string = appInsightsName
output logAnalyticsName string = logAnalyticsName
