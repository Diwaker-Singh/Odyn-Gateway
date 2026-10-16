# AzureRM 4.14.0 lacks a service-level APIM JSON schema resource. Use ARM,
# matching the existing Table/container deployment pattern without a new provider.
resource "azurerm_resource_group_template_deployment" "chat_schema" {
  name                = "chat-request-schema"
  resource_group_name = azurerm_resource_group.main.name
  deployment_mode     = "Incremental"
  template_content = jsonencode({
    "$schema"      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    contentVersion = "1.0.0.0"
    resources = [{
      type       = "Microsoft.ApiManagement/service/schemas"
      apiVersion = "2024-05-01"
      name       = "${azurerm_api_management.main.name}/chat-request-structure"
      properties = {
        schemaType  = "json"
        description = "Transport structure only; the application owns business policy."
        document    = jsondecode(file("${path.module}/chat-request.schema.json"))
      }
    }]
  })
}
