// CDPI POC infrastructure: every Azure resource the app's code currently
// reads credentials for (COSMOS_*, BLOB_*, DOCUMENT_INTELLIGENCE_*,
// AZURE_SPEECH_*, APPLICATIONINSIGHTS_*), nothing else. Deployed into one
// resource group so the whole thing can be deleted in one command and
// recreated identically later -- see deploy.ps1 / destroy.ps1.
//
// Deliberately NOT included:
// - Key Vault: for a throwaway POC loop, writing credentials to .env (what
//   the code already falls back to when AZURE_KEY_VAULT_URL isn't set) is
//   simpler than wiring secrets into Key Vault and re-populating it on
//   every recreate. Add it back for anything beyond a personal POC.
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
  properties: {
    serverFarmId: functionPlan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        {
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
          name: 'BLOB_CONNECTION_STRING'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosAccount.properties.documentEndpoint
        }
        {
          name: 'COSMOS_KEY'
          value: cosmosAccount.listKeys().primaryMasterKey
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDatabaseName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
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
// Outputs: deploy.ps1 reads these (via `az deployment group show`, not
// plain stdout) to write .env -- nothing here is printed to the console
// unencrypted by this template itself.
// ---------------------------------------------------------------------------
output storageAccountName string = storageAccount.name
output blobConnectionString string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output cosmosKey string = cosmosAccount.listKeys().primaryMasterKey
output cosmosDatabase string = cosmosDatabaseName
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint
output documentIntelligenceKey string = documentIntelligence.listKeys().key1
output speechKey string = speechService.listKeys().key1
output speechRegion string = location
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output functionAppName string = functionApp.name
