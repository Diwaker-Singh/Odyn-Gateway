# Cloud acceptance status

**Refreshed 11 October 2026 at 12:16 UTC.** The assessment is operational in
Azure, but cloud acceptance remains **partial**. The checks below were read
from the live deployment unless marked historical. They do not establish
unbounded production reliability or final billed cost.

## Current deployment

- Repository commit: `fcb13361fc36736a5f3cd58267c70b77a9c4fd49`.
- GitHub CI and the Hosted chat UI workflow both passed for that exact commit:
  [CI](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38135709389),
  [Pages](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38135709384).
- Azure Container App revision `ca-odyn-gateway-demo10--reasoningoff8cb4bd6`
  is healthy, receives 100% traffic and has two replicas. It is in live
  OpenRouter mode. The gateway image is pinned to
  `sha256:31b36049269ceb8aca650539f64e4da259688e4abf5c4d88214bcd3efecdfb13`;
  the collector is pinned to
  `sha256:81ea0fcd5fdc3666ea4f39bc8b885fee6b4da7ac04e687c11b97c6289aeb971a`.
- Since this revision became healthy at 11:04 UTC, Application Insights
  recorded 9 chat requests and 9 provider dependencies, with zero failed
  requests or dependencies. This is a short observation window, not a load or
  reliability guarantee.
- Front Door `/v1/healthz` returned HTTP 200 with HSTS. A harmless encoded XSS
  probe returned 403. The GitHub Pages CORS preflight returned HTTP 200 and
  allowed only the configured Pages origin, `POST`, and the required headers.
- The deployed Application Insights workbook was updated from the current
  Terraform source. A targeted follow-up plan was a no-op for that workbook.
  Six of seven workbook queries returned rows in the last six hours. The
  security/accounting panel had no rows in its last-hour window; five event
  categories appeared in a 24-hour query, consistent with an idle period.
  The workbook was not opened in the Azure portal for a visual-rendering check.
- One controlled authentication alert and its action-group notification path
  were previously demonstrated. This is the agreed single-alert proof; it does
  not claim every alert rule was independently forced and recovered.
- The latest available cost query is still the 04:35 UTC snapshot: INR 8.498
  month-to-date across the dedicated resource groups. A fresh query was
  throttled by Cost Management (HTTP 429), so actual cost remains unknown and
  this figure is not a bill or a spend cap.

## Remaining cloud limits

- **Direct-origin denial:** The APIM subnet NSG is associated with APIM, permits
  inbound 443 from `AzureFrontDoor.Backend`, and denies other inbound 80/443
  traffic. Front Door health succeeded; direct APIM access timed out after
  five seconds. Azure has no existing NSG/VNet flow logs, so packet-level
  denial evidence is unavailable and this requirement remains unproven. The
  Container Apps environment is internal; its direct hostname did not resolve
  from the external test client.
- **Terraform idempotence:** A fresh full plan had 9 in-place updates and no
  creates, deletes, replacements, or failed checks. The differences are APIM
  publisher metadata, seven alert descriptions, and Key Vault purge protection
  changing from disabled to enabled. The workbook-only update was applied and
  is now a no-op. The remaining plan was not applied because enabling purge
  protection is effectively irreversible and the other changes are unrelated
  to the tested runtime.
- **Limits and accounting:** The latest bounded two-replica saturation result
  remains historical, from revision 4 in stub mode: 225 requests, 112 HTTP
  429s, no HTTP 503s, and no matching conflict/table-error log lines. It does
  not prove global budgets, accounting correctness under contention, or live
  inference throughput. No load was sent to the live provider during this
  refresh.
- **Telemetry:** The dashboard queries and app/provider correlation have data,
  but APIM/Front Door diagnostic correlation has not been demonstrated.
- **Egress and identity separation:** Current evidence does not prove provider
  egress restriction or that deployment permissions are separated from
  runtime permissions.
- **Cost and lifecycle:** Cost Management throttled the refresh; charges may
  continue while the deployment remains active. Teardown has not been run.

## Reading these results

The hosted UI is at `https://diwaker-singh.github.io/Odyn-Gateway/`. Its public
configuration contains only public Entra client settings and the gateway
endpoint. The OpenRouter credential remains in Key Vault and is referenced by
the runtime; no secret value belongs in this repository.

Local tests, CI, Terraform validation, and prior stub-mode load results are
separate from live cloud proof. Do not describe the remaining items above as
passed. Raw plans, state, logs, prompts, responses, credentials, and private
test transcripts remain outside the public repository.

## Subsequent local policy change

The repository now contains APIM chat content/schema validation. This change
has passed local policy/schema and Terraform checks, but has not been applied
to Azure. The historical cloud observations above do not validate this new
policy. See [edge content validation](architecture.md#edge-content-validation)
and the [validation runbook](runbooks/request-validation.md) for required
post-deployment checks. The 17 shared schema fixtures and 259 passing gateway/
tooling tests are local results, not additional cloud acceptance evidence.
