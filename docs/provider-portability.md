# Cloud adapter portability

The Terraform module exposes a shared input/output contract and currently has
an implemented Azure adapter. The AWS adapter is a resource-free skeleton that
preserves the contract shape; it is not a deployable implementation and makes
no feature-parity claim.

| Capability | Azure implementation | AWS equivalent to implement and validate |
|---|---|---|
| Edge and API policy | Front Door/WAF and API Management | CloudFront/WAF and API Gateway or an equivalent managed edge |
| Runtime | Azure Container Apps | ECS/Fargate or Lambda with an ASGI adapter |
| Identity | Entra-issued JWTs and managed identity | Existing OIDC provider and task/service IAM role |
| Secret access | Key Vault reference via managed identity | Secrets Manager reference via task role |
| Shared admission | Azure Table transactional entities | DynamoDB conditional transactions or a managed Redis design |
| Telemetry | OpenTelemetry collector and Azure Monitor | OpenTelemetry collector with CloudWatch/X-Ray or another OTLP backend |

Porting the interface is not enough for parity. Each adapter must demonstrate
atomic reservation/settlement semantics, permission boundaries, private
network behavior, error mapping, cost controls and operational diagnostics in
its target platform. Keep provider-specific behavior in adapter modules rather
than branching on cloud names throughout gateway policy.
