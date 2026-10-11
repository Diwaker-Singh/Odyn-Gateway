locals {
  gateway_status_alerts = {
    auth       = { codes = ["401", "403"], threshold = 25, severity = 2, runbook = "auth-spike" }
    throttling = { codes = ["429"], threshold = 20, severity = 3, runbook = "429" }
  }
  application_alerts = {
    latency = {
      # Application Insights exposes request duration as numeric seconds here.
      # Keep the query result numeric for the scheduled-query metric threshold.
      query     = "requests | where name == 'gateway.chat' | summarize Value=percentile(duration,95)"
      threshold = 2
      runbook   = "5xx"
    }
    upstream = {
      query     = "customMetrics | where name == 'gateway.upstream.requests' and tostring(customDimensions.outcome) != 'ok' | summarize Value=sum(valueSum)"
      threshold = 5
      runbook   = "5xx"
    }
    accounting = {
      query     = "customMetrics | where name == 'gateway.events' and tostring(customDimensions.event) in ('budget_overshoot','budget_charged_unknown','store_unavailable') | summarize Value=sum(valueSum)"
      threshold = 0
      runbook   = "budget"
    }
    storage_conflicts = {
      query     = "customMetrics | where name == 'gateway.events' and tostring(customDimensions.event) == 'admission_conflict' | summarize Value=sum(valueSum)"
      threshold = 25
      runbook   = "table-contention"
    }
  }
}

resource "azurerm_monitor_metric_alert" "gateway_status" {
  for_each            = local.gateway_status_alerts
  name                = "alert-${local.name}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_api_management.main.id]
  severity            = each.value.severity
  frequency           = "PT5M"
  window_size         = "PT15M"
  description         = "Gateway ${each.key} response-rate alert. See the operational workbook for response guidance."
  criteria {
    metric_namespace = "Microsoft.ApiManagement/service"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = each.value.threshold
    dimension {
      name     = "GatewayResponseCode"
      operator = "Include"
      values   = each.value.codes
    }
  }
  dynamic "action" {
    for_each = azurerm_monitor_action_group.email
    content { action_group_id = action.value.id }
  }
  tags = local.common
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "application" {
  for_each                = local.application_alerts
  name                    = "alert-${local.name}-${each.key}"
  display_name            = "Odyn ${each.key}"
  location                = var.location
  resource_group_name     = azurerm_resource_group.main.name
  scopes                  = [azurerm_application_insights.main.id]
  severity                = 2
  evaluation_frequency    = "PT5M"
  window_duration         = "PT15M"
  enabled                 = true
  auto_mitigation_enabled = true
  skip_query_validation   = false
  description             = "Gateway ${each.key} telemetry alert. See the operational workbook for response guidance."
  criteria {
    query                   = each.value.query
    time_aggregation_method = "Maximum"
    metric_measure_column   = "Value"
    operator                = "GreaterThan"
    threshold               = each.value.threshold
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  dynamic "action" {
    for_each = length(azurerm_monitor_action_group.email) > 0 ? [true] : []
    content {
      action_groups     = [for group in azurerm_monitor_action_group.email : group.id]
      custom_properties = { runbook = "https://portal.azure.com/#resource/${azurerm_application_insights_workbook.gateway.id}" }
    }
  }
  tags = local.common
}
