# Root dispatch: exactly one adapter per `cloud`. No resources here.
module "azure" {
  source = "./azure"
  count  = var.cloud == "azure" ? 1 : 0

  environment                 = var.environment
  location                    = var.location
  tags                        = var.tags
  apim_sku                    = var.apim_sku
  idp_issuer_url              = var.idp_issuer_url
  idp_audience                = var.idp_audience
  delegated_scope             = var.delegated_scope
  machine_app_role            = var.machine_app_role
  allowed_algorithms          = var.allowed_algorithms
  model_allowlist             = var.model_allowlist
  model_prices                = var.model_prices
  price_table_version         = var.price_table_version
  live_enabled                = var.live_enabled
  cors_origins                = var.cors_origins
  browser_origins             = var.browser_origins
  max_body_bytes              = var.max_body_bytes
  max_tokens_ceiling          = var.max_tokens_ceiling
  per_principal_rps           = var.per_principal_rps
  per_principal_burst         = var.per_principal_burst
  global_rps                  = var.global_rps
  global_burst                = var.global_burst
  daily_token_limit           = var.daily_token_limit
  daily_spend_usd_limit       = var.daily_spend_usd_limit
  secret_ref                  = var.secret_ref
  upstream_mode               = var.upstream_mode
  network_enforced            = var.network_enforced
  alert_email                 = var.alert_email
  alert_notifications_enabled = var.alert_notifications_enabled
  collector_image             = var.collector_image
  container_image             = var.container_image
  min_replicas                = var.min_replicas
  image_registry_id           = var.image_registry_id
}

module "aws" {
  source = "./aws"
  count  = var.cloud == "aws" ? 1 : 0

  environment   = var.environment
  tags          = var.tags
  upstream_mode = var.upstream_mode
}
