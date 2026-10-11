# Azure adapter outputs. Secret NAME only — never the value.
output "gateway_url" {
  description = "Front Door gateway URL; APIM is the restricted origin."
  value       = "https://${azurerm_cdn_frontdoor_endpoint.main.host_name}"
}

output "secret_ref" {
  description = "Key Vault secret NAME. Value set out of band post-approval."
  value       = var.secret_ref
}

output "runtime_identities" {
  description = "Runtime managed identity principal IDs."
  value       = [azurerm_user_assigned_identity.api.principal_id]
}

output "dashboard_id" {
  description = "Operational workbook resource ID."
  value       = azurerm_application_insights_workbook.gateway.id
}

output "alarm_ids" {
  description = "Alert rule ids."
  value = concat(
    [azurerm_monitor_metric_alert.http_5xx.id],
    [for alert in azurerm_monitor_metric_alert.gateway_status : alert.id],
    [for alert in azurerm_monitor_scheduled_query_rules_alert_v2.application : alert.id],
  )
}
