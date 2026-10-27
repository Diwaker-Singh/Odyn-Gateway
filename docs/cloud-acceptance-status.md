# Cloud acceptance status

**Refreshed 28 October 2026 (IST).** There is currently no deployed Odyn Gateway
service in Azure. The `demo10` resource groups remain as empty anchors; the
separate `auto27` disposable environment used for pipeline validation was
deleted and verified absent. No Gateway compute or storage resources were found
in those groups. All seven Azure workflow enable switches are `false`, and the
temporary self-hosted runner has been removed. The GitHub Pages client remains
available, but its configured Azure endpoint is offline. Cloud acceptance is
**partial**: pipeline creation, release, deployment and destructive cleanup
were exercised, but that does not establish a currently available service or
close the application-level acceptance gaps below. Local success is not cloud
proof.

## Current pipeline validation (27–28 October)

The following Actions runs exercised the pipeline against disposable `auto27`
resources. They are operational evidence for those workflow paths, not a live
demo deployment:

- [Management-state setup, run 40515986303](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40515986303): passed.
- [Foundation bootstrap, run 40529879535](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40529879535): passed.
- [Image release, run 40567804118](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40567804118): passed.
- [Application deployment, run 40568660007](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40568660007): passed against the disposable environment.
- [Rollback workflow, run 40569830141](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40569830141): workflow and saved-plan path passed, but the selected image already matched the running image, so this was a no-op and **not a demonstrated rollback to an older release**.
- [Protected cleanup, run 40581291739](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40581291739): passed against the disposable stack. It encrypted and preserved all three Terraform states before deleting and verifying absence of the four target resource groups.
- [Exact-source CI, run 40576454490](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40576454490): passed on source `64427a1` before this status-document update.

The cleanup archive `encrypted-state-recovery` is retained as a GitHub Actions
artifact until 25 January 2027. It is a recovery copy, not an active Terraform
backend. Recreating the management and application state backends is required
before restoring it. The separate 24-hour cleanup-recovery artifact is
short-lived. The disposable management storage account and temporary runner
were removed after recovery was confirmed. All Azure workflow enable switches
are now disabled.

## Historical deployment and delivery observations

- [Application Actions run 38947234780](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38947234780)
  passed for source `7bca5c1`: the reviewed saved plan was applied, a full second
  plan returned no changes, the new revision became ready, and public
  health/HSTS, authentication rejection and exact-origin CORS checks passed.
- The last observed `demo10` revision was `ca-odyn-gateway-demo10--0000008`.
  Read-only checks on 19 October confirmed that revision and its APIM/schema
  resources were ready then. Those checks do not describe the current cloud
  inventory: the service is now offline.
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
- **Lifecycle:** The 28 October cleanup run deleted the complete disposable
  `auto27` application stack, including its API management, Key Vault and
  Container Apps resources, and verified its target groups absent after
  encrypted state recovery. It did not delete the separate `demo10` groups.
  The rollback workflow was a no-op; a real old-image rollback remains
  unproven. The 90-day archive is available for recovery, but its backends must
  be recreated first.
- **Cost:** No fresh attributable billing total was established by these
  pipeline runs. The 11 October INR 8.498 snapshot is historical, not current
  spend or a cap. The disposable test resources and runner were removed, but
  Azure billing data can be delayed; inspect Cost Management for current
  charges and any unrelated subscription resources.

## Reading these results

The hosted UI is at `https://diwaker-singh.github.io/Odyn-Gateway/`. Its public
configuration contains public client settings and the gateway endpoint. The
provider credential remains in Key Vault; no secret value belongs in source,
state outputs, logs, recordings or CI output.

Local revalidation on 19 October passed 338 tests plus formatting, types,
configuration, repository policy, compilation and secret checks. The later
pipeline runs validate their actual cloud operations only within the disposable
environment and exact source revisions listed above. They do not close the
remaining application acceptance gaps. Raw state, plans, logs, credentials and
private transcripts remain outside the public source tree.
