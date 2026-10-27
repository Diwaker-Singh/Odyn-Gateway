# Front Door Premium provides managed WAF and edge routing for this topology.
resource "azurerm_cdn_frontdoor_profile" "main" {
  name                = "fd-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  sku_name            = "Premium_AzureFrontDoor"
  tags                = local.common
}
resource "azurerm_cdn_frontdoor_endpoint" "main" {
  name                     = "fd-${local.name}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id
  tags                     = local.common
}
resource "azurerm_cdn_frontdoor_origin_group" "main" {
  name                     = "gateway"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id
  load_balancing {}
  health_probe {
    path                = "/v1/healthz"
    request_type        = "GET"
    protocol            = "Https"
    interval_in_seconds = 60
  }
}
resource "azurerm_cdn_frontdoor_origin" "main" {
  name                           = "apim"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.main.id
  enabled                        = true
  host_name                      = replace(azurerm_api_management.main.gateway_url, "https://", "")
  origin_host_header             = replace(azurerm_api_management.main.gateway_url, "https://", "")
  certificate_name_check_enabled = true
  http_port                      = 80
  https_port                     = 443
}
resource "azurerm_cdn_frontdoor_route" "main" {
  name                          = "gateway"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.main.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.main.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.main.id]
  # Enable Front Door's HTTP-to-HTTPS redirect while retaining HTTPS access.
  supported_protocols    = ["Http", "Https"]
  patterns_to_match      = ["/v1/*"]
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = true
  link_to_default_domain = true
}
resource "azurerm_cdn_frontdoor_firewall_policy" "main" {
  name                = replace("waf${local.name}", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  sku_name            = azurerm_cdn_frontdoor_profile.main.sku_name
  mode                = "Prevention"
  tags                = local.common
  managed_rule {
    type    = "Microsoft_DefaultRuleSet"
    version = "2.1"
    action  = "Block"
  }
}
resource "azurerm_cdn_frontdoor_security_policy" "main" {
  name                     = "waf"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id
  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.main.id
      association {
        patterns_to_match = ["/*"]
        domain { cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.main.id }
      }
    }
  }
}

resource "azurerm_api_management_api_operation" "health" {
  operation_id        = "healthz"
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Health probe"
  method              = "GET"
  url_template        = "/healthz"
}
resource "azurerm_api_management_api_operation_policy" "health" {
  operation_id        = azurerm_api_management_api_operation.health.operation_id
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  xml_content         = <<XML
<policies><inbound><return-response><set-status code="200" reason="OK" /><set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header><set-header name="Cache-Control" exists-action="override"><value>no-store</value></set-header><set-header name="Strict-Transport-Security" exists-action="override"><value>max-age=31536000</value></set-header><set-body>{"status":"ok"}</set-body></return-response></inbound><backend/><outbound/><on-error/></policies>
XML
}

# Authenticated operational readiness; static Front Door health is liveness only.
resource "azurerm_api_management_api_operation" "ready" {
  operation_id        = "readyz"
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Backend readiness (authenticated)"
  method              = "GET"
  url_template        = "/readyz"
}
resource "azurerm_api_management_api_operation_policy" "ready" {
  operation_id        = azurerm_api_management_api_operation.ready.operation_id
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  xml_content         = <<XML
<policies><inbound><base/><rewrite-uri template="/readyz" copy-unmatched-params="false" /></inbound><backend><base/></backend><outbound><base/></outbound><on-error><base/></on-error></policies>
XML
}
