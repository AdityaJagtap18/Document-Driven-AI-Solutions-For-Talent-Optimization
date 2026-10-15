// Phase 2 provisioning: Cosmos DB (serverless, NoSQL API), a Storage
// account (Standard LRS), and a Key Vault. See
// docs/azure-provisioning/phase2-cosmos-storage-keyvault.md for how to
// deploy this and what to do with the outputs.

@description('Globally unique Cosmos DB account name')
param cosmosAccountName string

@description('Globally unique Storage account name (lowercase/numbers only)')
param storageAccountName string

@description('Globally unique Key Vault name')
param keyVaultName string

@description('Azure region for all resources')
param location string = resourceGroup().location

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
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
  }
}

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
    accessPolicies: []
  }
}

output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output storageAccountId string = storageAccount.id
output keyVaultUri string = keyVault.properties.vaultUri
