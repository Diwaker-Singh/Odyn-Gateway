# Cloud acceptance status

**As of 11 October 2026.** The latest deployment and acceptance observations
recorded here were captured by 07:24 UTC; the cost query was captured at 04:35
UTC. This document refresh did not make a new Azure query. It is an operational
summary, not a substitute for timestamped evidence records, which are excluded
from the public repository. Cloud acceptance is **partial**; do not describe
the assessment as fully cloud-accepted.

## Demonstrated

| Area | Current evidence | Scope and limit |
| --- | --- | --- |
| Deployment | Azure revision `ca-odyn-gateway-demo10--0000007` was reported healthy, with two running replicas and 100% of traffic. Gateway and collector use digest-pinned images; deployed gateway source matched the workspace application source at the check. | Read-only health and image verification do not prove drift-free Terraform planning or teardown. |
| Identity | Human Entra PKCE and machine client-credentials demonstrations succeeded. An unauthenticated request was rejected with 401. | A complete JWT negative-case matrix and revocation/refresh behavior have not been demonstrated against the deployment. |
| Live provider | One bounded authenticated request through Front Door returned HTTP 200 from the live OpenRouter path. A correlated Application Insights record showed the gateway request and provider dependency succeeded. | One request does not establish reliability under repeated traffic; provider billed cost was not queried. |
| Alerting | A controlled authentication alert fired; Azure recorded an unsuppressed action-group invocation. The configured receiver was enrolled and received Azure's sample notification. | This demonstrates one alert path, not firing and recovery of every rule. |
| Telemetry | Seven workbook KQL queries were run against deployed Application Insights and each returned rows. Request/dependency correlation was observed for one successful live request. | The corrected workbook source was not applied to Azure at the last check; APIM/Front Door correlation remains incomplete. |
| Distributed throttling | A bounded run on revision 4 sent 225 requests to two healthy replicas in **stub mode**: 112 responses were 429, no 503s, and no matching table-conflict error lines were found. | This is not live inference throughput and does not prove global budget or cross-replica accounting correctness. It predates the current revision. |
| Local release and CI | The most recent local revalidation recorded 221 passing tests and 18 release checks; the image-inclusive report recorded 21/21 checks. GitHub Actions run [38129315335](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38129315335) passed for commit `0f67ff36c9c24d4548de5b52f0ff4e9ede3b2b6a`. | The CI run is for that commit, before the current documentation edits. Local checks and CI do not establish cloud behavior. Evidence files remain local and are not part of the public repository. |

## Still open

- **Direct-origin restriction:** a request directly to APIM timed out without a definitive response. The Azure inventory found no existing NSG or VNet flow logs. Denial of direct APIM access is unproven.
- **Dashboard publication:** the current workbook queries validated, but the corrected workbook update was not applied. The deployed workbook may still have an older provider-dependency filter.
- **Terraform idempotence and locking:** no current no-change plan or lock-contention demonstration is recorded.
- **Deployed limits and accounting:** the stub load run is useful bounded evidence, but global saturation, induced Table contention/retry behavior, and cross-replica accounting correctness remain unproven.
- **Egress, WAF and identity separation:** the release report does not have current acceptance evidence for provider egress restriction, WAF behavior, or least-privilege separation between deployment and runtime identities.
- **Teardown:** not performed. The Azure deployment and supporting resources remain active; ongoing charges may accrue.
- **Cost:** the last recorded Cost Management query, on 11 October 2026 at 04:35 UTC, showed INR 8.498 month-to-date across the four dedicated resource groups. Ingestion can lag; this is not a final invoice, remaining credit, forecast, or hard cap.

## Demo UI hosting

The static SPA is configured for GitHub Pages at
`https://diwaker-singh.github.io/Odyn-Gateway/`. Its public runtime settings,
Entra PKCE flow, and local preview are documented in
[hosted-chat-ui.md](hosted-chat-ui.md). The public site contains only public
client identifiers and the gateway URL; the OpenRouter key remains in Key
Vault. The Python loopback proxy at `127.0.0.1:8400` remains available for local
development. The Entra SPA redirect is registered. APIM returned HTTP 200 to
an unauthenticated CORS preflight from the exact Pages origin, allowing POST
with only `authorization` and `content-type`; an authenticated browser chat
smoke test remains to be recorded. GitHub Pages deployment is run by the
repository Actions workflow after the static UI changes reach `main`.

See Microsoft's [MSAL browser overview](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/about-msal-browser),
[authorization code with PKCE guidance](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow),
and the [GitHub Pages setup guide](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site).

## Status maintenance

Before presenting these results as current, repeat the cloud checks and update
the date, revision, evidence scope, and limitations together. Keep raw outputs,
tokens, prompts, responses, provider credentials, Terraform plans/state, and
private logs outside the public repository. Do not turn a local or stub-mode
result into a live-cloud acceptance claim.

The ignored local `evidence/submission-results.json` is an older generated
report with a stale source fingerprint and a blocked CI status. It is not the
current submission gate; use the exact-revision CI run above and rerun the
submission checks after any source changes.
