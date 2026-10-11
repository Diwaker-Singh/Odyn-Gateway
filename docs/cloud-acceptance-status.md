# Cloud acceptance status

**As of 11 October 2026 11:06 UTC.** Deployment, browser smoke and telemetry
checks were refreshed through 11:06 UTC; the cost query remains the 04:35 UTC
query. This is an operational summary, not a substitute for timestamped raw
evidence, which is excluded from the public repository. Cloud acceptance is
**partial**; do not describe the assessment as fully cloud-accepted.

## Demonstrated

| Area | Current evidence | Scope and limit |
| --- | --- | --- |
| Deployment | Azure revision `ca-odyn-gateway-demo10--reasoningoff8cb4bd6` is provisioned with two replicas. The gateway runs `acrodyngatewaydemo10.azurecr.io/odyn-gateway@sha256:31b36049269ceb8aca650539f64e4da259688e4abf5c4d88214bcd3efecdfb13`; the collector image was not changed. | The gateway image update used an image-only Container App update because the Terraform plan also proposed unrelated Key Vault purge-protection drift. A clean full Terraform plan and teardown are still unproven. |
| Identity | Human Entra PKCE and machine client-credentials demonstrations succeeded. An unauthenticated request was rejected with 401. | A complete JWT negative-case matrix and revocation/refresh behavior have not been demonstrated against the deployment. |
| Live provider | At 11:05 UTC, one bounded authenticated request from the hosted UI through Front Door returned HTTP 200 and visible text. Application Insights recorded the gateway request and both dependencies with HTTP 200. | The preceding intermittent 502 was traced to `no_text_token_limit`: the model's hidden reasoning used the UI's then-current 128-token cap before producing visible text. The gateway now sends `reasoning: {"enabled": false}` for chat completions; the bounded retest succeeded once. The UI source now uses a 512-token cap, a demo choice rather than a core gateway feature; that revised cap has not had a separate cloud smoke test. This does not establish reliability under repeated traffic or provider billed cost. |
| Alerting | A controlled authentication alert fired; Azure recorded an unsuppressed action-group invocation. The configured receiver was enrolled and received Azure's sample notification. | This demonstrates one alert path, not firing and recovery of every rule. |
| Telemetry | Seven workbook KQL queries were run against deployed Application Insights and each returned rows. Request/dependency correlation was observed for one successful live request. | The corrected workbook source was not applied to Azure at the last check; APIM/Front Door correlation remains incomplete. |
| Distributed throttling | A bounded run on revision 4 sent 225 requests to two healthy replicas in **stub mode**: 112 responses were 429, no 503s, and no matching table-conflict error lines were found. | This is not live inference throughput and does not prove global budget or cross-replica accounting correctness. It predates the current revision. |
| Local release and CI | The latest local run recorded 223 passing gateway tests, 9/9 policy checks, valid Terraform configuration in all four roots, 4 hosted UI tests, and 0 npm audit vulnerabilities. Exact-fix-commit CI [38133877561](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38133877561) passed for `8cb4bd6d5c80764c878021baac00b6e578ffaa0c`. | The separate Pages workflow validates and publishes the static UI. Local checks and CI do not establish cloud behavior. Evidence files remain local and are not part of the public repository. |

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
development. The Entra SPA redirect is registered. The Pages HTML and runtime
config both return successfully, and APIM returned HTTP 200 to an unauthenticated
CORS preflight from the exact Pages origin, allowing POST with only
`authorization` and `content-type`. An authenticated browser chat smoke test
now succeeds through Front Door and the live provider. The latest browser
request asked for a short fixed response and returned visible text within the
previous 128-token output cap. The current UI source uses a 512-token cap; this
revised setting has not had a separate cloud smoke test.

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
