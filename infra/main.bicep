// CDPI POC infrastructure: every Azure resource the app's code currently
// reads credentials for (COSMOS_*, BLOB_*, DOCUMENT_INTELLIGENCE_*,
// AZURE_SPEECH_*, APPLICATIONINSIGHTS_*), PLUS Key Vault as the real
// secret store every secret resolves through -- see deploy.ps1 / destroy.ps1.
//
// Key Vault wiring (added when this template moved from ".env-only POC"
// to "set everything up for real", matching the Secrets Management
// console and secrets_loader.py's Key Vault-first resolution this repo
// already has): every SECRET value this template produces (Cosmos key,
// Blob connection string, Document Intelligence key, Speech key, App
// Insights connection string) is written directly into Key Vault as a
// 'Microsoft.KeyVault/vaults/secrets' sub-resource, under the exact
// kebab-case naming secrets_loader.py's _get_from_keyvault() expects
// (COSMOS_KEY -> cosmos-key, etc.) -- NEVER as a plain Bicep `output`,
// so no secret value is retained in this deployment's ARM history the
// way an `output` would be. Only non-secret values (endpoints, database
// name, region, the vault's own URI) are outputs; deploy.ps1 writes
// those (plus AZURE_KEY_VAULT_URL) to .env and nothing else.
//
// Two identities get real Key Vault access via RBAC role assignments,
// not access policies (the modern, recommended mechanism):
// 1. The person running deploy.ps1 (their own `az login` identity,
//    passed in as `deployerPrincipalId`) gets "Key Vault Secrets
//    Officer" -- so secrets_loader.py's DefaultAzureCredential, used
//    locally via their own `az login` session, can read every secret
//    once they set AZURE_KEY_VAULT_URL in their local .env. No key ever
//    needs to be copied into .env by hand.
// 2. The deployed Function App gets a System-Assigned Managed Identity
//    and "Key Vault Secrets User" (read-only) -- its own app settings
//    for every secret are Key Vault REFERENCES (`@Microsoft.KeyVault(...)`),
//    resolved by Azure at runtime via that managed identity. The actual
//    secret value is never stored in the Function App's own
//    configuration at all -- this is the "prefer Managed Identity"
//    requirement every Azure config spec this session asked for,
//    actually wired up, not just documented as a future option.
//
// Deliberately still NOT included:
// - Azure AI Search: semantic-library/'s AzureAISearchVectorStore is
//   written but not wired into any code path yet -- nothing to connect it
//   to today.
// - Any frontend hosting (Static Web Apps, App Service, storage static
//   website): frontend stays localhost-only per explicit instruction --
//   this template provisions backend/data services only.
// - WhatsApp Business Cloud API: that's Meta's platform, not Azure --
//   nothing to provision here.
//
// *** UNTESTED. *** No Azure CLI/Bicep tooling is available in the
// environment this was written in, so this has only been reviewed against
// the ARM/Bicep resource API docs -- it has never actually been deployed.
// Review it yourself before running deploy.ps1 against a real
// subscription, and expect to fix something on the first real attempt.

@description('Short name used as a prefix for every resource (lowercase letters/numbers only, keep it short -- Storage Account names max 24 chars total).')
@minLength(3)
@maxLength(12)
param namePrefix string = 'cdpipoc'

@description('Azure region for every resource. Not every region has capacity for every SKU used here (especially Free-tier Cognitive Services) -- change this if deployment fails with a capacity/quota error.')
param location string = resourceGroup().location

@description('A short random/unique suffix so re-running this template after a destroy does not collide with soft-deleted resource names still reserved from the last run (see destroy.ps1 for the purge step that avoids needing this in practice).')
param uniqueSuffix string = uniqueString(resourceGroup().id)

@description('The Azure AD object id of the person running deploy.ps1 (az ad signed-in-user show --query id -o tsv) -- granted real read/write access to the Key Vault this template creates. Required so a human running this locally can actually use the secrets it provisions.')
param deployerPrincipalId string

@description('Email to receive a budget alert at 80% and a forecasted-100% warning. Leave empty to skip creating a budget alert entirely. Strongly recommended if you are working with a small, fixed credit balance -- this is the main cost guardrail this template provides.')
param alertEmail string = ''

@description('Monthly budget cap in USD that triggers the alert above. Default (5) leaves a safety margin under a $7 credit balance -- lower it if your actual balance is smaller. This is an ALERT, not a hard spending cap: Azure (outside a free-trial-type subscription) does not stop resources from running just because a budget alert fired, so still check actual spend yourself (see README) rather than relying on this alone.')
param budgetAmountUsd int = 5

param _budgetStartDate string = utcNow('yyyy-MM-01')

var storageAccountName = toLower('${namePrefix}st${uniqueSuffix}')
var cosmosAccountName = toLower('${namePrefix}-cosmos-${uniqueSuffix}')
var cosmosDatabaseName = 'cdpi'
var docIntelName = toLower('${namePrefix}-docintel-${uniqueSuffix}')
var speechName = toLower('${namePrefix}-speech-${uniqueSuffix}')
var logAnalyticsName = '${namePrefix}-logs-${uniqueSuffix}'
var appInsightsName = '${namePrefix}-appi-${uniqueSuffix}'
var functionPlanName = '${namePrefix}-plan-${uniqueSuffix}'
var functionAppName = toLower('${namePrefix}-func-${uniqueSuffix}')
var shortSuffix = substring(uniqueSuffix, 0, 6) // Key Vault names are capped at 24 chars -- the full uniqueSuffix is too long once namePrefix is near its own 12-char max
var keyVaultName = toLower('${namePrefix}-kv-${shortSuffix}')

// Built-in role definition IDs (RBAC role assignments reference these by
// GUID, not name) -- "Key Vault Secrets Officer" and "Key Vault Secrets User".
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

// ---------------------------------------------------------------------------
// Storage: holds the Blob containers (raw-resumes, parsed-resumes -- created
// lazily by context-lake/client.py's get_blob_container() on first use, not
// here) and doubles as the Function App's required internal storage.
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------------------------------------------------------------------------
// Cosmos DB: serverless capacity mode -- pay per request, zero cost while
// idle, no provisioned-throughput bill running in the background between
// POC sessions. Containers (candidates, interview_transcripts,
// scoring_history, audit_log) are created lazily by
// context-lake/client.py's get_container() on first use, not here.
// ---------------------------------------------------------------------------
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-11-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-11-15' = {
  parent: cosmosAccount
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
  }
}

// ---------------------------------------------------------------------------
// Document Intelligence (document_intelligence_client.py). F0 free tier:
// one per subscription per region, 500 pages/month.
// ---------------------------------------------------------------------------
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: docIntelName
  location: location
  kind: 'FormRecognizer'
  sku: {
    name: 'F0'
  }
  properties: {
    customSubDomainName: docIntelName
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Speech (voicebot/azure_speech_utils.py). F0 free tier: 5 audio hours/month.
// ---------------------------------------------------------------------------
resource speechService 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: speechName
  location: location
  kind: 'SpeechServices'
  sku: {
    name: 'F0'
  }
  properties: {
    customSubDomainName: speechName
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Key Vault -- the real secret store secrets_loader.py resolves against
// once AZURE_KEY_VAULT_URL is set. RBAC authorization (not legacy access
// policies) -- the two role assignments below are how the deployer and
// the Function App actually get access; see this file's header comment.
// ---------------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource deployerKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerPrincipalId, keyVaultSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------------
// Observability (observability.py / APPLICATIONINSIGHTS_CONNECTION_STRING).
// Workspace-based App Insights is the only kind new accounts can use now.
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Every SECRET value this template produces, written directly into Key
// Vault -- never through an `output` (see this file's header comment).
// Names match secrets_loader.py's kv_name = name.lower().replace('_','-')
// convention exactly, so secrets_loader.get_secret("COSMOS_KEY") finds
// "cosmos-key" here with zero code changes.
// ---------------------------------------------------------------------------
// Each secret resource explicitly depends on deployerKeyVaultAccess --
// the role assignment granting the ARM deployment's own identity
// permission to write here. Without this dependsOn, Bicep has no reason
// to order these after the role assignment (nothing else references
// it), and a same-deployment "create role assignment, then immediately
// use it" sequence can occasionally hit RBAC propagation delay (a few
// minutes is possible) even with correct ordering -- a transient 403 on
// a secret write here, on a first deploy attempt, is that delay, not a
// template bug; re-running deploy.ps1 after a minute resolves it.
resource cosmosKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-key'
  properties: {
    value: cosmosAccount.listKeys().primaryMasterKey
  }
  dependsOn: [
    deployerKeyVaultAccess
  ]
}

resource blobConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'blob-connection-string'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
  }
  dependsOn: [
    deployerKeyVaultAccess
  ]
}

resource documentIntelligenceKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'document-intelligence-key'
  properties: {
    value: documentIntelligence.listKeys().key1
  }
  dependsOn: [
    deployerKeyVaultAccess
  ]
}

resource azureSpeechKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-speech-key'
  properties: {
    value: speechService.listKeys().key1
  }
  dependsOn: [
    deployerKeyVaultAccess
  ]
}

resource appInsightsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'applicationinsights-connection-string'
  properties: {
    value: appInsights.properties.ConnectionString
  }
  dependsOn: [
    deployerKeyVaultAccess
  ]
}

// ---------------------------------------------------------------------------
// Azure Functions (azure-functions/) -- Consumption plan (Y1): scales to
// zero, no cost while not actively handling requests, which is the whole
// point of a spin-up/tear-down POC loop.
// ---------------------------------------------------------------------------
resource functionPlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: functionPlanName
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true // required for Linux consumption
  }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        {
          // Required by the Functions runtime itself at cold-start, before
          // Key Vault reference resolution is available -- left as a raw
          // value rather than a Key Vault reference for that reason (a
          // well-known exception, not an oversight).
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          // Key Vault reference -- resolved by Azure at runtime via this
          // Function App's own system-assigned managed identity (granted
          // "Key Vault Secrets User" below). The actual connection string
          // is never stored in this app setting.
          name: 'BLOB_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(SecretUri=${blobConnectionStringSecret.properties.secretUri})'
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosAccount.properties.documentEndpoint
        }
        {
          name: 'COSMOS_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${cosmosKeySecret.properties.secretUri})'
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDatabaseName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(SecretUri=${appInsightsConnectionStringSecret.properties.secretUri})'
        }
      ]
    }
  }
}

resource functionAppKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Cost guardrail: a budget alert on this resource group, since the
// Free/Serverless/Consumption SKUs above should cost close to $0 while
// idle but real usage still bills (Cosmos RUs, Document Intelligence/
// Speech pages/minutes past the free grant, Functions executions past the
// free grant). Skipped entirely if alertEmail is left blank.
// ---------------------------------------------------------------------------
resource budgetAlert 'Microsoft.Consumption/budgets@2023-11-01' = if (!empty(alertEmail)) {
  name: '${namePrefix}-budget'
  properties: {
    category: 'Cost'
    amount: budgetAmountUsd
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: _budgetStartDate
    }
    notifications: {
      ActualGreaterThan80Percent: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        contactEmails: [alertEmail]
        thresholdType: 'Actual'
      }
      ForecastedGreaterThan100Percent: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: [alertEmail]
        thresholdType: 'Forecasted'
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs: NON-SECRET values only -- every secret this template produces
// lives in Key Vault (see the secret resources above), never in an
// `output` (ARM retains output values in this deployment's history in
// plaintext, which an `output` of a secret would defeat the point of
// using Key Vault at all). deploy.ps1 reads these to write the repo's
// .env file with endpoints/database/region/the vault's own URI --
// AZURE_KEY_VAULT_URL plus these is everything secrets_loader.py needs
// to resolve every secret from Key Vault from here on.
// ---------------------------------------------------------------------------
output storageAccountName string = storageAccount.name
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output cosmosDatabase string = cosmosDatabaseName
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint
output speechRegion string = location
output keyVaultUri string = keyVault.properties.vaultUri
output functionAppName string = functionApp.name
