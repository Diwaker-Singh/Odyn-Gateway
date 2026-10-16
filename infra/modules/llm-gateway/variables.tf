# Shared contract inputs (both adapters). Only inputs with real consumers.
variable "cloud" {
  description = "Target cloud adapter: azure (implemented) or aws (skeleton)."
  type        = string
  validation {
    condition     = contains(["azure", "aws"], var.cloud)
    error_message = "cloud must be azure or aws."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region (provisional: Central India)."
  type        = string
  default     = "centralindia"
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default     = {}
}

variable "contract_version" {
  description = "Module interface version."
  type        = string
  default     = "v1"
}

variable "apim_sku" {
  description = "APIM Developer_1 for bounded non-production proof; no production SLA."
  type        = string
  default     = "Developer_1"
  validation {
    condition     = var.apim_sku == "Developer_1"
    error_message = "This reviewed classic VNET topology implements Developer_1 only; Standard v2 requires separate network/provider design review."
  }
}

variable "idp_issuer_url" {
  description = "Entra ID v2.0 issuer URL (tenant-specific)."
  type        = string
}

variable "idp_audience" {
  description = "Gateway API application (client) ID expected as aud."
  type        = string
}

variable "delegated_scope" {
  description = "Delegated scope required on human tokens."
  type        = string
  default     = "chat.invoke"
}

variable "machine_app_role" {
  description = "Application role required on machine tokens."
  type        = string
  default     = "Chat.Invoke.Application"
}

variable "allowed_algorithms" {
  description = "JWT signing algorithm allowlist. Never include none."
  type        = list(string)
  default     = ["RS256"]
  validation {
    condition     = var.allowed_algorithms == tolist(["RS256"])
    error_message = "This adapter implements RS256 only."
  }
}

variable "model_allowlist" {
  description = "Alias to provider model id mapping (one live alias to start)."
  type        = map(string)
  default     = { default = "openrouter/auto" }
}

variable "max_body_bytes" {
  description = "Raw request body cap."
  type        = number
  default     = 32768
}

variable "max_tokens_ceiling" {
  description = "Max output tokens; over-ceiling requests are rejected, not clamped."
  type        = number
  default     = 1024
}

variable "per_principal_rps" {
  description = "Sustained per-principal request rate."
  type        = number
  default     = 5
}

variable "per_principal_burst" {
  description = "Per-principal burst capacity."
  type        = number
  default     = 10
}

variable "global_rps" {
  description = "Sustained global request rate."
  type        = number
  default     = 20
}

variable "global_burst" {
  description = "Global burst capacity."
  type        = number
  default     = 40
}

variable "daily_token_limit" {
  description = "Daily token ceiling applied separately to each principal and the aggregate global budget."
  type        = number
  default     = 100000
}

variable "daily_spend_usd_limit" {
  description = "Daily USD ceiling applied separately to each principal and the aggregate global budget. Enforced alongside tokens."
  type        = number
  default     = 5
}

variable "secret_ref" {
  description = "Key Vault secret NAME for the provider key. Value is set out of band; never a value here."
  type        = string
  default     = "openrouter-api-key"
}

variable "upstream_mode" {
  description = "stub (default/test) or live. Live requires separate key + spend approval."
  type        = string
  default     = "stub"
  validation {
    condition     = contains(["stub", "live"], var.upstream_mode)
    error_message = "upstream_mode must be stub or live."
  }
}

variable "network_enforced" {
  description = "True only after a bypass-denied test proves private origin. App secret/header is never proof."
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Action-group email destination. Kept in ignored local tfvars, never committed."
  type        = string
  default     = ""
  sensitive   = true
}

variable "container_image" {
  description = "Reviewed registry image pinned by SHA256 digest; no placeholder deployment."
  type        = string
  validation {
    condition     = can(regex("^[^ ]+@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "container_image must use a registry digest."
  }
}

variable "min_replicas" {
  type    = number
  default = 1
  validation {
    condition     = var.min_replicas >= 1 && var.min_replicas <= 2 && floor(var.min_replicas) == var.min_replicas
    error_message = "min_replicas must be an integer from 1 through 2."
  }
}

variable "collector_image" {
  type        = string
  description = "Reviewed patched collector image, pinned by registry digest."
  validation {
    condition     = can(regex("^[^ ]+@sha256:[a-f0-9]{64}$", var.collector_image))
    error_message = "Collector image must use an immutable registry digest."
  }
}

variable "live_enabled" {
  description = "Explicit approval gate for live provider dispatch; defaults false."
  type        = bool
  default     = false
}
variable "price_table_version" {
  description = "Reviewed price source/date identifier; demo prices cannot enable live dispatch."
  type        = string
  default     = "2026-10-09-demo"
}
variable "model_prices" {
  description = "Nonsecret reviewed per-1K token prices, keyed by model alias."
  type        = map(object({ input_usd_per_1k = number, output_usd_per_1k = number }))
  default     = { default = { input_usd_per_1k = 0.001, output_usd_per_1k = 0.002 } }
  validation {
    condition     = alltrue([for price in values(var.model_prices) : price.input_usd_per_1k >= 0 && price.output_usd_per_1k >= 0])
    error_message = "Model prices must be nonnegative."
  }
}
variable "cors_origins" {
  description = "Explicit browser origins; empty disables cross-origin browser access."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for origin in var.cors_origins : can(regex("^https://[A-Za-z0-9.-]+(:[0-9]+)?$", origin))])
    error_message = "CORS origins must be explicit HTTPS origins, never wildcards."
  }
}

variable "browser_origins" {
  description = "Exact public browser origins allowed by the API edge CORS policy."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for origin in var.browser_origins : can(regex("^https://[A-Za-z0-9.-]+(:[0-9]+)?$|^http://(localhost|127\\.0\\.0\\.1|\\[::1\\])(:[0-9]+)?$", origin))])
    error_message = "Browser origins must be exact HTTPS origins or loopback-only HTTP origins, without paths or wildcards."
  }
}

variable "alert_notifications_enabled" {
  description = "False for testing; enable only for the final approved delivery demonstration."
  type        = bool
  default     = false
}

variable "image_registry_id" {
  description = "Dedicated ACR ARM ID for managed-identity pulls; empty for public non-ACR images."
  type        = string
  default     = ""
}

variable "vault_purge_protection" {
  type    = bool
  default = true
}
