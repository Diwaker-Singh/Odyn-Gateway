# Cloud acceptance status

**Refreshed 19 October 2026 (IST).** The Azure assessment deployment was
retired at the owner's request to stop charges; provisioning is disabled.
The deployment observations below are historical, not a current availability
claim. Cloud acceptance remains **partial**: delivery automation has
passed, while several security, load and operational claims below retain
explicit limits. Local success is not cloud proof.

## Pre-retirement deployment and delivery

- [Application Actions run 38947234780](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38947234780)
  passed for source `7bca5c1`: the reviewed saved plan was applied, a full second
  plan returned no changes, the new revision became ready, and public
  health/HSTS, authentication rejection and exact-origin CORS checks passed.
- The last deployed revision was `ca-odyn-gateway-demo10--0000008`. Read-only checks
  on 19 October confirmed that it is the latest ready revision, APIM reports
  `Succeeded`, and the `chat-request-schema` deployment reports `Succeeded`.
  Runtime images are pinned to the verified remote manifest references from
  [publication run 38946401639](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38946401639).
- The rollout preserved model/limits configuration, Key Vault configuration,
  and alert thresholds/receivers. Runtime settings were compared privately
  against the saved plan; image references matched the publication receipt.
- Image publication, state-storage provisioning, registry reconciliation,
  management-storage creation, operational inventory and Pages have passing
  Actions runs. See [workflow execution status](workflow-coverage.md#execution-status)
  for their exact source and scope. CI and cloud jobs are separate claims.
- Delivery now uses dedicated federated service principals; the runtime uses
  its separate managed identity. This demonstrates identity separation in the
  Actions path. Production role minimization and independent-review governance
  still need environment-specific review.

- [Independent execution test 39163453137](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39163453137)
  passed validation, runner creation, the self-hosted tool/credential-isolation
  probe and automatic retirement on 19 October. Test infrastructure, registration,
  temporary identity/permissions and expiring test secrets were removed. The
  original live revision remained ready, with HTTP 200/HSTS afterward. Full
  topology cold recreation and the persistent GitHub App/bootstrap trust path
  are not established by this isolated test.

## Historical application observations

These observations predate the latest pipeline rollout and were not repeated
by its safe smoke checks:

- On 11 October, an authenticated hosted-UI chat and its provider dependency
  returned HTTP 200 after the reasoning/token-budget fix. Human PKCE and
  machine client-credentials authentication were demonstrated.
- One controlled authentication alert and its action-group notification path
  were demonstrated. This is the agreed single-alert proof, not proof that
  all seven alert rules were independently forced and recovered.
- The deployed workbook's queries returned application data; a targeted
  workbook follow-up plan was clean. Portal visual rendering and complete
  edge-to-provider diagnostic correlation were not demonstrated.
- A bounded two-replica run on an earlier **stub-mode** revision recorded
  225 requests, 112 HTTP 429s, no HTTP 503s, and no matching conflict/table-error
  log lines. It does not prove live inference throughput or complete global
  budget enforcement under contention.

## Remaining acceptance limits

- **Direct-origin denial:** Earlier Front Door health succeeded and direct
  APIM access timed out. NSG configuration supports the intended boundary, but
  no packet/flow evidence established the denial. The Container Apps backend
  is internal. Keep direct-origin denial unproven rather than treating a
  timeout as definitive proof.
- **Content validation:** APIM schema and policy deployment passed through
  Actions. Seventeen shared schema fixtures validate structure locally;
  authenticated malformed-body cases have not been replayed through APIM.
  See the [validation runbook](runbooks/request-validation.md).
- **Limits/accounting:** The earlier stub run is qualified evidence only.
  Global budget contention, live-provider capacity and sustained reliability
  have not been fully established. No new inference/load traffic was generated
  by the delivery verification.
- **Telemetry and egress:** Useful app/provider telemetry exists historically;
  complete APIM/Front Door trace correlation and network-enforced provider-domain
  egress restrictions remain unproven.
- **Lifecycle:** Live deployment teardown has not run. [Disposable cleanup passed](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39158982129)
  after a group-metadata permission fix, using empty application/registry groups
  and a real state-storage account. This does not demonstrate complete live
  APIM/Key Vault/Container Apps teardown. Recovered state and protected approvals
  remain mandatory before destructive execution.
- **Cost:** There is no fresh attributable billing result for this refresh.
  The 11 October INR 8.498 snapshot is historical, not today's cost or a cap.
  The retained demo and management storage continue to incur charges. Review
  Azure Cost Management and the [cleanup scope](pipeline-lifecycle.md#6-remaining-costs-and-the-management-boundary).

## Reading these results

The hosted UI is at `https://diwaker-singh.github.io/Odyn-Gateway/`. Its public
configuration contains public client settings and the gateway endpoint. The
provider credential remains in Key Vault; no secret value belongs in source,
state outputs, logs, recordings or CI output.

Local revalidation on 19 October passed 338 tests plus formatting, types,
configuration, repository policy, compilation and secret checks. Those checks,
image scans and historical application observations do not close the remaining
cloud limits above. Raw state, plans, logs, credentials and private transcripts
remain outside the public source tree.
