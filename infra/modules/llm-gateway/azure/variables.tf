# Azure adapter inputs (subset of the shared contract, same names/types).
variable "environment" { type = string }
variable "location" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
variable "apim_sku" { type = string }
variable "idp_issuer_url" { type = string }
variable "idp_audience" { type = string }
variable "delegated_scope" { type = string }
variable "machine_app_role" { type = string }
variable "allowed_algorithms" { type = list(string) }
variable "model_allowlist" { type = map(string) }
variable "max_body_bytes" {
  type = number
  validation {
    condition     = var.max_body_bytes >= 1 && var.max_body_bytes <= 4194304 && floor(var.max_body_bytes) == var.max_body_bytes
    error_message = "APIM content validation requires a whole-byte limit between 1 and 4 MiB."
  }
}
variable "max_tokens_ceiling" { type = number }
variable "per_principal_rps" { type = number }
variable "per_principal_burst" { type = number }
variable "global_rps" { type = number }
variable "global_burst" { type = number }
variable "daily_token_limit" { type = number }
variable "daily_spend_usd_limit" { type = number }
variable "secret_ref" { type = string }
variable "upstream_mode" { type = string }
variable "network_enforced" { type = bool }
variable "alert_email" {
  type      = string
  default   = ""
  sensitive = true
}
variable "container_image" { type = string }

# Keep the default inexpensive for normal plans; V9 can request two replicas
# explicitly for a distributed throttling test without editing module source.
variable "min_replicas" {
  type    = number
  default = 1
  validation {
    condition     = var.min_replicas >= 1 && var.min_replicas <= 2 && floor(var.min_replicas) == var.min_replicas
    error_message = "min_replicas must be an integer from 1 through 2."
  }
}

variable "collector_image" { type = string }

variable "live_enabled" { type = bool }
variable "price_table_version" { type = string }
variable "model_prices" {
  type = map(object({ input_usd_per_1k = number, output_usd_per_1k = number }))
}
variable "cors_origins" { type = list(string) }
variable "browser_origins" { type = list(string) }

variable "alert_notifications_enabled" { type = bool }

variable "image_registry_id" { type = string }
