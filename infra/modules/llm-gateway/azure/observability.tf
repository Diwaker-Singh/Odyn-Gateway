# Workbook source and queries are versioned; real ingestion/query execution is
# The deterministic GUID avoids an extra random-provider dependency.
resource "azurerm_application_insights_workbook" "gateway" {
  name                = uuidv5("url", "${azurerm_resource_group.main.id}/odyn-gateway-observability")
  display_name        = "Odyn Gateway operations"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  # The AzureRM provider validates this field as lowercase, while Azure
  # resource IDs are case-insensitive.
  source_id   = lower(azurerm_application_insights.main.id)
  category    = "workbook"
  description = "RED, dependency, authentication, throttling and accounting queries; cloud validation required."
  data_json = jsonencode(merge(jsondecode(file("${path.module}/../../../telemetry/workbook.json")), {
    fallbackResourceIds = [azurerm_application_insights.main.id]
  }))
  tags = local.common
}
