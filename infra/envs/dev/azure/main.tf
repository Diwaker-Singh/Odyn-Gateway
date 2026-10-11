# Dev environment: Azure adapter call. Files only — NO init/plan/apply.
# Backend is Blob Storage with lease locking (bootstrap separately, gated).
terraform {
  required_version = "= 1.9.8"
  backend "azurerm" {
    resource_group_name  = "rg-odyn-gateway-tfstate"
    storage_account_name = "stodyngatewaytfstate"
    container_name       = "tfstate"
    key                  = "dev.terraform.tfstate"
    use_azuread_auth     = true
  }
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.14.0"
    }
  }
}

module "llm_gateway" {
  source = "../../../modules/llm-gateway"

  cloud                       = "azure"
  environment                 = var.environment
  location                    = var.location
  tags                        = { project = "odyn-gateway", env = "dev" }
  apim_sku                    = "Developer_1"
  idp_issuer_url              = var.idp_issuer_url
  idp_audience                = var.idp_audience
  collector_image             = var.collector_image
  container_image             = var.container_image
  min_replicas                = var.min_replicas
  image_registry_id           = var.image_registry_id
  alert_email                 = var.alert_email
  alert_notifications_enabled = var.alert_notifications_enabled
  upstream_mode               = var.upstream_mode
  live_enabled                = var.live_enabled
  model_allowlist             = var.model_allowlist
  model_prices                = var.model_prices
  price_table_version         = var.price_table_version
  cors_origins                = var.cors_origins
}

variable "idp_issuer_url" { type = string }
variable "idp_audience" { type = string }
variable "container_image" { type = string }
variable "min_replicas" {
  type    = number
  default = 1
  validation {
    condition     = var.min_replicas >= 1 && var.min_replicas <= 2 && floor(var.min_replicas) == var.min_replicas
    error_message = "min_replicas must be an integer from 1 through 2."
  }
}
variable "alert_email" {
  type      = string
  default   = ""
  sensitive = true
}
variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = can(regex("^[a-z0-9]{2,8}$", var.environment))
    error_message = "Use a short lowercase environment suffix for globally unique resource names."
  }
}
variable "location" {
  type    = string
  default = "centralindia"
}

variable "collector_image" { type = string }

variable "upstream_mode" {
  type    = string
  default = "stub"
}
variable "live_enabled" {
  type    = bool
  default = false
}
variable "price_table_version" {
  type    = string
  default = "2026-10-09-demo"
}
variable "model_allowlist" {
  type    = map(string)
  default = { default = "openrouter/auto" }
}
variable "model_prices" {
  type    = map(object({ input_usd_per_1k = number, output_usd_per_1k = number }))
  default = { default = { input_usd_per_1k = 0.001, output_usd_per_1k = 0.002 } }
}
variable "cors_origins" {
  type    = list(string)
  default = []
}

variable "alert_notifications_enabled" {
  type    = bool
  default = false
}

variable "image_registry_id" {
  type    = string
  default = ""
}
