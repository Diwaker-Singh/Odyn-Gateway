# Platform-native gateway traces correlate to the application's OTel W3C spans.
# Bodies, bearer credentials, client IP and caller identity headers are excluded.
resource "azurerm_api_management_logger" "gateway" {
  name                = "applicationinsights"
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  resource_id         = azurerm_application_insights.main.id
  application_insights { instrumentation_key = azurerm_application_insights.main.instrumentation_key }
}
resource "azurerm_api_management_api_diagnostic" "gateway" {
  identifier                = "applicationinsights"
  api_name                  = azurerm_api_management_api.chat.name
  api_management_name       = azurerm_api_management.main.name
  resource_group_name       = azurerm_resource_group.main.name
  api_management_logger_id  = azurerm_api_management_logger.gateway.id
  always_log_errors         = true
  sampling_percentage       = 100
  http_correlation_protocol = "W3C"
  log_client_ip             = false
  verbosity                 = "information"
  frontend_request {
    body_bytes     = 0
    headers_to_log = ["X-Request-Id"]
  }
  frontend_response {
    body_bytes     = 0
    headers_to_log = ["X-Request-Id"]
  }
  backend_request {
    body_bytes     = 0
    headers_to_log = ["X-Request-Id"]
  }
  backend_response {
    body_bytes     = 0
    headers_to_log = ["X-Request-Id"]
  }
}
