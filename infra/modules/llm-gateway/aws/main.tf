# AWS skeleton: no resources exist. Outputs are placeholders so the
# shared contract validates; conformance reports not_applied everywhere.
output "gateway_url" {
  value = ""
}
output "secret_ref" {
  value = ""
}
output "runtime_identities" {
  value = []
}
output "dashboard_id" {
  value = ""
}
output "alarm_ids" {
  value = []
}
output "conformance" {
  value = {
    jwt_validation      = "not_applied"
    per_principal_limit = "not_applied"
    token_budget        = "not_applied"
    private_origin      = "not_applied"
    waf                 = "not_applied"
    telemetry_otel      = "not_applied"
    dashboard           = "not_applied"
    alerts              = "not_applied"
  }
}
