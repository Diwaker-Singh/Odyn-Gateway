# Reviewed bootstrap proposal only. Initial bootstrap state stays in an ignored
# private location; application state uses Blob leases and Entra authentication.
terraform {
  required_version = "= 1.9.8"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "= 4.14.0" }
  }
}
variable "subscription_id" {
  type      = string
  sensitive = true
}
variable "tenant_id" {
  type      = string
  sensitive = true
}
provider "azurerm" {
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  resource_provider_registrations = "none"
  storage_use_azuread             = true
  features {}
}
variable "resource_group_name" { type = string }
variable "storage_account_name" { type = string }
variable "location" { type = string }
variable "operator_object_id" { type = string }
variable "approved_ipv4_ranges" {
  type      = list(string)
  sensitive = true
  validation {
    condition     = length(var.approved_ipv4_ranges) > 0 && alltrue([for ip in var.approved_ipv4_ranges : ip != "0.0.0.0/0"])
    error_message = "Explicit operator IPv4 allowlist required; never allow the Internet."
  }
}
resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location
  tags     = { project = "odyn-gateway", purpose = "terraform-state", managed_by = "terraform" }
}
resource "azurerm_storage_account" "state" {
  # checkov:skip=CKV_AZURE_59:Development state endpoint is default-deny/IP-allowlisted with Entra-only auth; use private connectivity for production runners.
  # checkov:skip=CKV_AZURE_36:Trusted-services bypass intentionally disabled; only the explicit operator allowlist is permitted.
  # checkov:skip=CKV2_AZURE_33:Development state uses a narrow operator-IP allowlist; production runners should use private connectivity.
  # checkov:skip=CKV2_AZURE_1:Microsoft-managed encryption is used; customer-managed keys require a separately operated key lifecycle.
  # checkov:skip=CKV_AZURE_206:LRS is a development default; select zone/redundant storage for availability requirements.
  # checkov:skip=CKV2_AZURE_41:Shared keys disabled; no account SAS is issued.
  # checkov:skip=CKV_AZURE_33:State has no Queue workload; Blob versioning/retention is enabled.
  name                            = var.storage_account_name
  resource_group_name             = azurerm_resource_group.state.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true
  tags                            = { project = "odyn-gateway", purpose = "terraform-state", managed_by = "terraform" }
  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
    ip_rules       = var.approved_ipv4_ranges
  }
  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 7 }
    container_delete_retention_policy { days = 7 }
  }
}
resource "azurerm_role_assignment" "operator" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.operator_object_id
}
# The Blob container is created through ARM, avoiding account keys and the
# provider's data-plane container initialization during RBAC propagation.
resource "azurerm_resource_group_template_deployment" "container" {
  name                = "state-container"
  resource_group_name = azurerm_resource_group.state.name
  deployment_mode     = "Incremental"
  template_content = jsonencode({
    "$schema"      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    contentVersion = "1.0.0.0"
    resources = [{
      type       = "Microsoft.Storage/storageAccounts/blobServices/containers"
      apiVersion = "2023-05-01"
      name       = "${azurerm_storage_account.state.name}/default/tfstate"
      properties = { publicAccess = "None" }
    }]
  })
}
