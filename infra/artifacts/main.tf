# Container registry for reviewed application images.
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
  features {}
}
variable "environment" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9]{2,8}$", var.environment))
    error_message = "Use a short dedicated environment suffix."
  }
}
variable "location" { type = string }
variable "registry_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9]{5,50}$", var.registry_name))
    error_message = "ACR requires a globally unique alphanumeric name."
  }
}
resource "azurerm_resource_group" "artifacts" {
  name     = "rg-odyn-gateway-artifacts-${var.environment}"
  location = var.location
  tags     = { project = "odyn-gateway", environment = var.environment, managed_by = "terraform" }
}
resource "azurerm_container_registry" "images" {
  # Basic is a development registry. Use Premium for private endpoints, geo-replication or higher availability.
  # checkov:skip=CKV_AZURE_237:Dedicated endpoints require Premium; this development registry uses authenticated public pulls.
  # checkov:skip=CKV_AZURE_233:Zone redundancy is not enabled in the single-region development environment.
  # checkov:skip=CKV_AZURE_167:Basic does not support native retention; lifecycle cleanup is handled by the operator.
  # checkov:skip=CKV_AZURE_164:Deployments pin reviewed image digests; signature enforcement can be added with a registry trust policy.
  # checkov:skip=CKV_AZURE_165:Multi-region registry replication is not enabled in this environment.
  # checkov:skip=CKV_AZURE_166:Images are scanned in CI; managed quarantine is not configured.
  # checkov:skip=CKV_AZURE_163:Images are scanned in CI; registry Defender scanning is not configured.
  # checkov:skip=CKV_AZURE_139:Basic has an authenticated public endpoint; admin/anonymous pulls are disabled. Use Premium for private endpoints.
  name                   = var.registry_name
  resource_group_name    = azurerm_resource_group.artifacts.name
  location               = var.location
  sku                    = "Basic"
  admin_enabled          = false
  anonymous_pull_enabled = false
  tags                   = azurerm_resource_group.artifacts.tags
}
output "registry_id" { value = azurerm_container_registry.images.id }
output "login_server" { value = azurerm_container_registry.images.login_server }
