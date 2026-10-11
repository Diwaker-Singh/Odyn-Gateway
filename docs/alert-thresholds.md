# Alert design

Terraform defines alerts for request failures, authentication spikes,
throttling, gateway latency, upstream failures, accounting/store anomalies and
Table contention. Thresholds are configuration defaults and should be tuned
from a measured baseline for each environment. They are not spending caps.

| Signal | Example condition | Operator response |
|---|---|---|
| HTTP failures | Elevated 5xx rate | Correlate gateway, edge and provider outcomes; check revision health and store availability. |
| Authentication spike | Elevated 401/403 count | Separate invalid credentials from insufficient permission; check identity configuration and recent changes. |
| Throttling | Elevated 429 count | Confirm clients honor `Retry-After`; compare against configured rate and budget limits. |
| Gateway latency | Route latency above baseline | Split admission-store latency from provider dependency latency. |
| Upstream failures | Provider error/timeout increase | Check provider status, request deadlines and breaker state; avoid replaying ambiguous requests. |
| Accounting/store anomaly | Unknown usage, overshoot or unavailable store | Keep admission fail-closed and investigate reservation/settlement consistency. |
| Table contention | Elevated conditional-write conflicts | Reduce offered load and inspect hot partition contention and operation latency. |

The scheduled-query metrics use interval/delta semantics to avoid repeatedly
summing cumulative counters. Conflict signals are aggregate and do not include
principal or request identifiers. Alert actions should point to the matching
[runbook](runbooks/).

The current rules do not by themselves cover unhealthy revisions, secret-access
anomalies, certificate/JWKS expiry, cost forecasts, or notification-channel
availability. Add suitable platform diagnostics and test both firing and
recovery for each additional rule. A budget alert is advisory; it cannot
enforce a hard spend ceiling.
