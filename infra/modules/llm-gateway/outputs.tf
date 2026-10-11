# Shared contract outputs. Never a secret value: secret_ref is a name only.
output "gateway_url" {
  description = "Public gateway endpoint URL."
  value       = var.cloud == "azure" ? try(module.azure[0].gateway_url, "") : ""
}

output "secret_ref" {
  description = "Secret NAME only. The value is set out of band post-approval."
  value       = var.cloud == "azure" ? try(module.azure[0].secret_ref, "") : ""
}

output "runtime_identities" {
  description = "Runtime managed identity principal IDs."
  value       = var.cloud == "azure" ? try(module.azure[0].runtime_identities, []) : []
}

output "dashboard_id" {
  description = "Dashboard resource id where deployed."
  value       = var.cloud == "azure" ? try(module.azure[0].dashboard_id, "") : ""
}

output "alarm_ids" {
  description = "Alert rule ids where deployed."
  value       = var.cloud == "azure" ? try(module.azure[0].alarm_ids, []) : []
}

output "tls_policy_enforced" {
  description = "True only with a custom domain + verified policy (unavailable: always false here)."
  value       = false
}

output "contract_version" {
  description = "Echo of the interface version."
  value       = var.contract_version
}

output "conformance" {
  description = "Per-guarantee native/emulated/unsupported/not_applied/not_tested map."
  value = {
    jwt_validation      = var.cloud == "azure" ? "not_tested" : "not_applied"
    per_principal_limit = "not_tested"
    token_budget        = "not_tested"
    private_origin      = var.network_enforced ? "native" : "not_tested"
    waf                 = var.cloud == "azure" ? "not_tested" : "not_applied"
    telemetry_otel      = "not_tested"
    dashboard           = var.cloud == "azure" ? "not_tested" : "not_applied"
    alerts              = var.cloud == "azure" ? "not_tested" : "not_applied"
  }
}
