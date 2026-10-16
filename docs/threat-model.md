# Security model

## Trust boundaries

The service accepts untrusted HTTP input and bearer tokens, relies on an
external identity provider and shared admission store, and can send requests to
a paid model provider. The deployment control plane can change application
images, network rules, managed identities and secret references. Treat each
boundary as independently configurable and test it in the target environment.

| Threat | Controls in this repository | Residual concern |
|---|---|---|
| Forged or over-privileged tokens | RS256 allowlist, issuer/audience/time validation, trusted bounded JWKS refresh, separate scope and role checks | A stolen valid bearer remains usable until expiry unless the API has an immediate-revocation mechanism. |
| Malformed or oversized requests | Chat-only APIM media-type, byte-limit and structural validation; independent application streamed-body and semantic checks | Edge policy is locally validated and awaits deployment. Test chunked requests and validation overhead; a missing deployed schema can also produce rejections. |
| Request or cost exhaustion | Streamed body cap, schema/output bounds, continuous rate buckets, atomic shared admission, daily budgets, fail-closed storage behavior | Shared partition throughput, configured price accuracy and token-estimation error need capacity validation. |
| Duplicate paid dispatch | Request-wide deadline; retry only after a proven pre-send connection failure; conservative reservation settlement | A lost response after upstream processing remains ambiguous and can still incur provider charges. |
| SSRF or credential leakage | Fixed HTTPS destination, redirects disabled, bounded replies, explicit safe headers, secret-manager reference | Application destination checks do not replace network egress restrictions. |
| Secret disclosure | Managed identity reference, no secret in Terraform inputs, hidden local prompt, redacted errors and telemetry | Secret rotation, access auditing and state/plan access require deployment operations. |
| Origin bypass | Infrastructure includes edge, private ingress and origin controls | Validate effective network policy from permitted and prohibited paths; configuration alone is not proof. |
| Sensitive telemetry | Allowlisted fields, hashed principal identifiers, no prompts/completions/tokens or raw claims | Review platform diagnostics and collector exporters whenever telemetry changes. |
| Compromised build or deployment | Hashed Python locks, Terraform provider locks, digest-pinned container bases, CI scans, guarded saved-plan workflow | Protect repository actions, build credentials, state storage and deployment identity. |

## Safe failure behavior

Authentication and schema failures are rejected before provider dispatch.
The new APIM policy is defense in depth; application validation remains required
for local clients and any future cloud adapter. The [validation runbook](runbooks/request-validation.md)
explains the safe 400/413/415 edge errors and application 422 errors. Payloads
and verbose APIM validation details must not be added to incident notes or
custom telemetry.
Admission-store failures deny new work rather than falling back to process
memory. Unknown provider usage retains a conservative reservation. Circuit
breaker state is process-local. Provider error bodies and credentials are not
returned to callers or written to logs.

## Deployment responsibilities

Use least-privileged, separate deployment and runtime identities. Restrict
provider egress at the network layer, keep runtime ingress private behind the
chosen edge, enable audit logs for secret and state access, and apply retention
and alerting policies that fit the deployment. Validate denial and recovery
paths from the deployed environment before relying on those controls.
