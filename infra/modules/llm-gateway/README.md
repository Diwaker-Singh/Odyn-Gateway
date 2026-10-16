# Shared gateway contract and cloud adapters

The module dispatches on `var.cloud`: Azure is the implemented primary adapter;
AWS is a resource-free skeleton. The shared contract is in `variables.tf` and
`outputs.tf` (`contract_version = "v1"`); the conformance map distinguishes
native, emulated, unsupported, and not-applied guarantees.

- `versions.tf`: Terraform `= 1.9.8`, AzureRM `= 4.14.0`; lockfiles pin the
  provider package checksums for supported platforms.
- `azure/`: Front Door/WAF, APIM, private Container Apps ingress, managed
  identity, Table admission store, Key Vault reference, Monitor/Insights,
  dashboards, alerts and diagnostics.
- `aws/`: provider-free skeleton mirroring the public module contract. It does
  not create AWS resources. See [provider portability](../../../docs/provider-portability.md).
- `../../envs/dev/azure/`: Azure dev composition and remote Blob backend
  configuration. Actual backend values stay in private ignored configuration.

Run `make release` from the repository root for local tests, formatting,
Terraform validation, TFLint, Checkov, dependency, secret and image checks.
Validation does not contact Azure. Cloud plan/apply/destroy are separate
operator actions.

## Azure chat validation

`azure/chat-request.schema.json` defines the transport structure for chat.
`azure/content-validation.tf` registers it as an APIM service-level schema
through an incremental ARM deployment; `azure/main.tf` ensures it exists before
applying the referencing policy. No additional Terraform provider is required.
`max_body_bytes` is shared with the runtime and must be an integer from 1 to
4,194,304 bytes. Business policy stays in application validation.

This addition is locally tested and is not yet deployed. See
[edge validation](../../../docs/architecture.md#edge-content-validation) and the
[validation runbook](../../../docs/runbooks/request-validation.md) before rollout.
