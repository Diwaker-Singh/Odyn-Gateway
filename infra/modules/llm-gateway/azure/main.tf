# Azure adapter: APIM -> Container Apps -> Table ledger. Stub default.
# No secret VALUES anywhere: only the Key Vault secret NAME is referenced.
# No custom domain: platform HTTPS endpoint; tls_policy_enforced stays false.
locals {
  name   = "odyn-gateway-${var.environment}"
  common = merge(var.tags, { environment = var.environment, managed_by = "terraform" })
  model_config = {
    for alias, model in var.model_allowlist : alias => {
      provider_model_id = model
      input_usd_per_1k  = try(var.model_prices[alias].input_usd_per_1k, -1)
      output_usd_per_1k = try(var.model_prices[alias].output_usd_per_1k, -1)
    }
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.common
}

resource "azurerm_api_management" "main" {
  # checkov:skip=CKV_AZURE_174:External APIM is restricted to the configured Front Door path and protected by origin checks.
  name                 = "apim-${local.name}"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  publisher_name       = "Odyn Gateway"
  publisher_email      = "noreply@example.invalid"
  sku_name             = var.apim_sku
  tags                 = local.common
  virtual_network_type = "External"
  virtual_network_configuration { subnet_id = azurerm_subnet.apim.id }
  security {
    enable_frontend_ssl30 = false
    enable_frontend_tls10 = false
    enable_frontend_tls11 = false
    enable_backend_ssl30  = false
    enable_backend_tls10  = false
    enable_backend_tls11  = false
  }
  depends_on = [azurerm_subnet_network_security_group_association.apim]
}

resource "azurerm_api_management_api" "chat" {
  name                  = "llm-chat"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main.name
  revision              = "1"
  display_name          = "LLM Chat"
  path                  = "v1"
  protocols             = ["https"]
  subscription_required = false
}

resource "azurerm_api_management_api_operation" "chat" {
  operation_id        = "post-chat"
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "POST /v1/chat"
  method              = "POST"
  url_template        = "/chat"
}

# Edge auth: Entra JWT validation. Coarse per-key throttle is BEST-EFFORT
# only (tier-dependent); authoritative limits live in app + Table Storage.
resource "azurerm_api_management_api_policy" "chat" {
  api_name            = azurerm_api_management_api.chat.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  xml_content = templatefile("${path.module}/policy.xml.tftpl", {
    issuer       = var.idp_issuer_url
    audience     = var.idp_audience
    burst        = var.global_burst
    backend      = azurerm_container_app.main.ingress[0].fqdn
    frontdoor_id = azurerm_cdn_frontdoor_profile.main.resource_guid
  })
}

resource "azurerm_user_assigned_identity" "api" {
  name                = "id-${local.name}-api"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
}

resource "azurerm_container_app_environment" "main" {
  name                               = "cae-${local.name}"
  location                           = azurerm_resource_group.main.location
  resource_group_name                = azurerm_resource_group.main.name
  infrastructure_subnet_id           = azurerm_subnet.apps.id
  internal_load_balancer_enabled     = true
  infrastructure_resource_group_name = "rg-${local.name}-managed"
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.common
}

resource "azurerm_container_app" "main" {
  workload_profile_name = "Consumption"
  lifecycle {
    precondition {
      condition     = length(var.model_allowlist) > 0 && toset(keys(var.model_allowlist)) == toset(keys(var.model_prices))
      error_message = "Every allowlisted model must have exactly one reviewed price entry."
    }
    precondition {
      condition     = var.upstream_mode != "live" || (var.live_enabled && length(trimspace(var.price_table_version)) > 0 && !endswith(var.price_table_version, "demo"))
      error_message = "Live mode requires explicit approval and sourced model pricing."
    }
  }

  name                         = "ca-${local.name}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  depends_on                   = [azurerm_resource_group_template_deployment.ledger_table, azurerm_role_assignment.api_table_data, azurerm_role_assignment.api_kv_reader, azurerm_role_assignment.registry_pull]
  revision_mode                = "Single"
  tags                         = local.common

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  # Provider-key delivery is deliberately separate from Terraform state. In
  # live mode, Container Apps resolves this versionless Key Vault reference
  # with its managed identity. Terraform never receives or stores the value.
  # Stub mode does not require a provider key and therefore creates no secret
  # reference, allowing zero-key cloud validation.
  dynamic "secret" {
    for_each = var.upstream_mode == "stub" ? [] : [var.secret_ref]
    content {
      name                = "openrouter-api-key"
      identity            = azurerm_user_assigned_identity.api.id
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value}"
    }
  }

  ingress {
    # Reachable from the VNET through the internal environment VIP, not public Internet.
    external_enabled = true
    target_port      = 8000
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  dynamic "registry" {
    for_each = var.image_registry_id == "" ? [] : [1]
    content {
      server   = split("/", var.container_image)[0]
      identity = azurerm_user_assigned_identity.api.id
    }
  }

  template {
    container {
      name   = "gateway"
      image  = var.container_image
      cpu    = 0.5
      memory = "1Gi"
      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/readyz"
        interval_seconds        = 10
        timeout                 = 3
        failure_count_threshold = 3
        success_count_threshold = 1
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/healthz"
        interval_seconds        = 30
        timeout                 = 3
        failure_count_threshold = 3
        initial_delay           = 30
      }
      startup_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/healthz"
        interval_seconds = 5
        timeout          = 3
        # Azure Container Apps accepts thresholds up to 30; at a 5-second
        # interval this preserves 150 seconds of startup grace.
        failure_count_threshold = 30
      }
      env {
        name  = "UPSTREAM_MODE"
        value = var.upstream_mode
      }
      env {
        name  = "IDP_ISSUER_URL"
        value = var.idp_issuer_url
      }
      dynamic "env" {
        for_each = {
          GATEWAY_ENVIRONMENT         = "azure"
          IDP_AUDIENCE                = var.idp_audience
          JWKS_URL                    = "${trimsuffix(var.idp_issuer_url, "/v2.0")}/discovery/v2.0/keys"
          AZURE_CLIENT_ID             = azurerm_user_assigned_identity.api.client_id
          TABLE_ENDPOINT              = azurerm_storage_account.ledger.primary_table_endpoint
          TABLE_NAME                  = "ledger"
          DELEGATED_SCOPE             = var.delegated_scope
          MACHINE_APP_ROLE            = var.machine_app_role
          MAX_BODY_BYTES              = tostring(var.max_body_bytes)
          MAX_TOKENS                  = tostring(var.max_tokens_ceiling)
          PER_PRINCIPAL_RPS           = tostring(var.per_principal_rps)
          PER_PRINCIPAL_BURST         = tostring(var.per_principal_burst)
          GLOBAL_RPS                  = tostring(var.global_rps)
          GLOBAL_BURST                = tostring(var.global_burst)
          DAILY_TOKEN_LIMIT           = tostring(var.daily_token_limit)
          DAILY_SPEND_USD             = tostring(var.daily_spend_usd_limit)
          ODYN_LIVE_UPSTREAM_APPROVED = var.live_enabled ? "1" : "0"
          PRICE_TABLE_VERSION         = var.price_table_version
          MODEL_PRICES_JSON           = jsonencode(local.model_config)
          CORS_ORIGINS                = join(",", var.cors_origins)
          OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318"
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        # Stub deployments have no provider secret at all. Live mode maps the
        # Container Apps secret reference to the process environment.
        for_each = var.upstream_mode == "stub" ? [] : [1]
        content {
          name        = "OPENROUTER_API_KEY"
          secret_name = "openrouter-api-key"
        }
      }
    }
    container {
      name   = "telemetry"
      image  = var.collector_image
      cpu    = 0.25
      memory = "0.5Gi"
      args   = ["--config=env:ODYN_OTEL_CONFIG"]
      env {
        name  = "ODYN_OTEL_CONFIG"
        value = file("${path.module}/../../../telemetry/collector.yaml")
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.main.connection_string
      }
    }
    min_replicas = var.min_replicas
    max_replicas = 2
  }
}

resource "azurerm_storage_account" "ledger" {
  # checkov:skip=CKV2_AZURE_1:Microsoft-managed encryption is used; customer-managed keys require a separately operated key lifecycle.
  # checkov:skip=CKV_AZURE_206:LRS is a configurable development default; select zone/redundant storage for availability requirements.
  # checkov:skip=CKV2_AZURE_41:No account-SAS issued; shared key disabled below, so SAS cannot be minted from keys.
  # checkov:skip=CKV_AZURE_33:No Queue workload. Table data-plane audit is configured with StorageRead/Write/Delete diagnostics.
  name                            = "stodyn${var.environment}ledger"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  public_network_access_enabled   = false
  tags                            = local.common

  blob_properties {
    delete_retention_policy {
      days = 7
    }
  }

  sas_policy {
    expiration_period = "00.01:00:00"
  }
}


# Runtime reads Table data via Microsoft Entra RBAC (shared key disabled).
resource "azurerm_role_assignment" "api_table_data" {
  scope                = azurerm_storage_account.ledger.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_resource_group_template_deployment" "ledger_table" {
  name                = "table-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  deployment_mode     = "Incremental"
  template_content = jsonencode({
    "$schema"      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    contentVersion = "1.0.0.0"
    resources = [{
      type       = "Microsoft.Storage/storageAccounts/tableServices/tables"
      apiVersion = "2023-05-01"
      name       = "${azurerm_storage_account.ledger.name}/default/ledger"
      properties = {}
    }]
  })
}

# Storage data-plane audit (table/blob reads+writes) to the workspace.
resource "azurerm_monitor_diagnostic_setting" "ledger" {
  name                       = "diag-${local.name}-ledger"
  target_resource_id         = "${azurerm_storage_account.ledger.id}/tableServices/default"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "StorageRead"
  }

  enabled_log {
    category = "StorageWrite"
  }

  enabled_log {
    category = "StorageDelete"
  }

  metric {
    category = "Transaction"
    enabled  = true
    retention_policy {
      enabled = false
      days    = 0
    }
  }

  metric {
    category = "Capacity"
    enabled  = false
    retention_policy {
      enabled = false
      days    = 0
    }
  }
}

resource "azurerm_key_vault" "main" {
  # Purge protection prevents irreversible deletion during routine cleanup.
  name                          = "kv-${local.name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  soft_delete_retention_days    = 90
  purge_protection_enabled      = true
  enable_rbac_authorization     = true
  public_network_access_enabled = false
  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
  tags = local.common
}

data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "api_kv_reader" {
  # Stub deployments have no Key Vault secret reference. Grant data-plane
  # access only when an explicitly approved live deployment consumes it.
  count                = var.upstream_mode == "live" ? 1 : 0
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.1
  tags                = local.common
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "other"
  tags                = local.common
}

# Notifications are disabled by default. Supply a recipient only through
# protected environment configuration; never commit an address.
resource "azurerm_monitor_action_group" "email" {
  count               = var.alert_notifications_enabled && var.alert_email != "" ? 1 : 0
  name                = "ag-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "odynag"
  email_receiver {
    name          = "owner"
    email_address = var.alert_email
  }
  tags = local.common
}

resource "azurerm_monitor_metric_alert" "http_5xx" {
  name                = "alert-${local.name}-5xx"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_api_management.main.id]
  description         = "Sev2: elevated 5xx. See the linked operational runbooks for response guidance."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  criteria {
    metric_namespace = "Microsoft.ApiManagement/service"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 10
    dimension {
      name     = "GatewayResponseCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }
  dynamic "action" {
    for_each = azurerm_monitor_action_group.email
    content {
      action_group_id = action.value["id"]
    }
  }
  tags = local.common
}

# Pull-only identity permission, separate from deploy image publication.
resource "azurerm_role_assignment" "registry_pull" {
  count                = var.image_registry_id == "" ? 0 : 1
  scope                = var.image_registry_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}
