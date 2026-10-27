# Workflow coverage and execution

CI and manual workflows exercise the repository tooling at the appropriate
boundary. Interactive sign-in and secret entry remain operator tasks; running
those unattended would change their security model. No workflow deletes the
live deployment automatically.

## Script coverage

| Script or component | Pipeline coverage |
|---|---|
| `policy-guard.py` | CI policy job executes it. |
| `check-configs.py` | CI scan job parses source/configuration and checks policy, schema and workflow safeguards. |
| `scan_repo.py` | Local pre-push candidate/history scan; CI's Gitleaks job independently scans committed history. |
| `release-check.py` | Local aggregate command. CI runs the corresponding test, build, type, infrastructure, policy and security gates as separate jobs; it does not invoke this aggregate runner. |
| `image-check.py`, `source_fingerprint.py` | CI builds runtime images, verifies source/platform parity and runs isolated non-root smoke checks. |
| `delivery.py` | Unit tests exercise provenance, destination checks, encryption/tampering/expiry, foundation preparation and fake publication. Real commands run only in configured release, foundation and application workflows. |
| `deploy.sh`, `deploy.py` | CI executes the default local preflight wrapper after backend-free Terraform initialization. Guarded operator plan/apply paths have regression tests; real CI delivery uses `delivery.py` and scoped federation instead of the Owner-bound legacy driver. |
| `execution.py`, `lifecycle.py` | Bootstrap ownership/identity/token/adoption boundaries and orchestration/failure tests; protected runner probe and retirement workflow. Complete topology recreation is a separate acceptance test. |
| `management.py` | Secure ARM template, destination and configuration unit tests; manual management-state workflow; real isolated Azure storage creation passed. |
| `cleanup.py` | Encryption, approval expiry/source/run/identity binding, inventory drift, confirmation, durable recovery and ordered-deletion tests; protected cleanup passed on the disposable fixture. Full live-topology deletion remains untested. |
| `teardown.py` | Unit tests mock cloud calls and deletion/recovery failures. Operational Actions offers read-only inventory. The separate protected cleanup workflow uses its ownership and ordered-deletion methods after durable encrypted state recovery. |
| `serve_stub.py`, `test-local-load.py`, `k6-limits.js` | CI starts a loopback fixture server and runs the bounded two-principal load scenario. Fixture tokens never authenticate against Azure or the live provider. |
| `cloud-smoke.py` | Unit tests cover URL restrictions and safe requests. Manual Operational checks → health performs three public requests: health, unauthenticated rejection, and CORS preflight. No valid chat request is sent. |
| `local_chat_ui.py`, `local-ui/` | Unit/integration tests cover the local proxy and fixture behavior. It is not deployed as a public server. |
| `sso-demo.py` | CLI parsing is exercised and identity behavior has fixture tests. Real human PKCE needs a person/browser; a real machine demo needs its separately configured certificate. Neither is established by fixture success. |
| `set-provider-key.sh` | Shell syntax and repository hygiene guards run. Real secret entry stays in the hidden operator prompt, outside Actions and Terraform. |
| `hosted-ui/` | Pages runs locked install, configuration tests, dependency audit and static build/deploy. |

## Validation-only dispatch

Release, application deployment, foundation provisioning and Operational checks
default to `validation_only=true`. This runs the reusable credential-free helper
suite and skips cloud jobs. A successful validation-only workflow is helper
proof, not publication, Terraform application, or deployed acceptance.

For a real configured operation, explicitly set `validation_only=false`. The
release and application workflows additionally require `AZURE_DELIVERY_ENABLED`;
foundation requires `AZURE_FOUNDATION_ENABLED`; read-only inventory requires
`AZURE_OPERATIONS_ENABLED`. All Azure flags remain disabled until setup.
Public-edge health needs only the configured public gateway URL.

Examples after the changes are on main:

```sh
gh workflow run release.yml -f validation_only=true
gh workflow run deploy.yml -f validation_only=true
gh workflow run infrastructure.yml -f validation_only=true -f component=bootstrap
gh workflow run operations.yml -f validation_only=false -f action=health
```

For actual delivery, follow [foundation provisioning](foundation-deployment.md)
and [application delivery](delivery.md). Dispatch application deployment with
a successful exact-source **publication** run ID, not a validation-only release:
the latter has no `verified-release` artifact and cannot supply deployable images.
For foundation runs, choose bootstrap or registry deliberately.

The read-only inventory path uses protected environment `azure-operations`, a
dedicated OIDC identity with Reader on the four assessment groups, and matching
`AZURE_SUBSCRIPTION_ID`/`AZURE_TENANT_ID`. Set its environment `AZURE_CLIENT_ID`;
optional repository variables `AZURE_ENVIRONMENT_SUFFIX` and
`BOOTSTRAP_RESOURCE_GROUP` override the assessment defaults. Inventory needs no
delete permissions and never passes `--execute` to teardown.

## Execution status

Cloud delivery and cleanup pipelines were exercised on disposable resources
through 28 October 2026 (IST). The last test stack has been removed; no current
Azure Gateway deployment is claimed.

| Operation | Actual result |
|---|---|
| Exact-source CI | [40576454490](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40576454490), passed for source `64427a1`; inspect all 12 jobs, including image scans. |
| Management-state setup | [40515986303](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40515986303), passed on the disposable `auto27` environment. |
| Foundation bootstrap | [40529879535](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40529879535), passed on `auto27`. |
| Image release | [40567804118](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40567804118), passed on `auto27`. |
| Application deployment | [40568660007](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40568660007), passed on `auto27`. |
| Rollback workflow | [40569830141](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40569830141), workflow and saved-plan path passed, but the plan was a no-op because the selected image already matched. This was **not** a demonstrated rollback to a previous image. |
| Cleanup | [40581291739](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/40581291739), passed on the disposable `auto27` stack: encrypted recovery of three Terraform states, ordered deletion, and verification that all four target groups were absent. |

The temporary `auto27` deployment, registry, state resources, automation-state
storage and self-hosted runner were removed after verification. The disposable
execution resource group remains empty. The encrypted `encrypted-state-recovery`
Actions artifact (artifact ID `12770017148`) expires 25 January 2027; it is not
an active backend. Recreate management and application state backends before
restoring the archive. A separate short-lived cleanup-recovery artifact expires
24 hours after upload. The current Pages client is not proof of a running API.
All seven Azure pipeline enable switches are `false`.

### Earlier component evidence

These runs are historical and do not describe the current Azure inventory:

- [Private execution bootstrap](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39163453137) passed validation, isolated runner creation, tool/credential-isolation probe and automatic retirement.
- [Application deployment](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38947234780) passed for `7bca5c1`, including a clean second Terraform plan, revision readiness, and public health/authentication/CORS checks.
- [Image publication](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38946401639) published and verified both images. Foundation and management-state creation also passed in [38883057735](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38883057735) and [38945385264](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38945385264).
- The corrected app-authenticated runner retirement is recorded in [39211831808](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39211831808). The earlier first run was cancelled before obsolete retirement code completed.

OIDC and role setup was tested with disposable identities and scopes; one-time
trust and protected environment setup remain operator prerequisites for future
use. These component runs do not establish a single full cold-start orchestration.

The 19 October disposable cold controller [39212823565](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39212823565)
did not pass its protected execution gate and remains historical. The later
27–28 October component pipeline runs successfully exercised management,
foundation, release, deployment, rollback workflow and destructive cleanup on
`auto27`, but did not prove a single cold-start orchestration of every component
from an empty subscription. No valid chat/provider request was part of those
pipeline tests.

Interactive human SSO, secret entry and administrative identity grants remain
intentional operator boundaries. Credential-free success does not establish
cloud provisioning, deletion or production qualification.

### Cleanup test scope and failure learning

The 27–28 October `auto27` cleanup test used three real Terraform states and a
deployed disposable stack. It encrypted and preserved all three states before
ordered deletion, then verified the four target groups were absent. This now
demonstrates cleanup of the disposable APIM, Key Vault, Container Apps and
supporting resources, not teardown of the separate `demo10` environment. The
separate demo groups were not targeted and are currently empty by inventory.

Earlier attempts failed closed because group-scoped permissions could not
establish absence of a missing group. The approved cleanup identity now has
subscription-level resource-group metadata read; delete rights remain scoped to
the explicitly named target groups. The successful run established final
absence after scoped role assignments were removed. Only encrypted state
recovery was published; no plain state or encryption key was included.

### Independent execution test scope

The 19 October execution test used a temporary OIDC principal with Contributor
only on its disposable execution group and subscription-level group metadata
read/create permission. It used expiring registration/removal tokens; no general
GitHub credential was persisted. The GitHub App minting path, restoration of
delivery grants after full cleanup, and optional application peering/private-DNS
paths were not exercised by this fixture. Those remain acceptance work for a
fully configured cold recreation.

The probe caught a missing HOME in the managed-command environment; startup now
supplies an explicit clean user environment. Fresh-backend, CLI compatibility,
lease ownership and skipped-cloud-job failures have regression guards. All test
resources, registrations, temporary permissions/identity and test secrets were
removed, and mutation flags were disabled. The unchanged live revision returned
HTTP 200 with HSTS afterward. No provider call or live resource deletion occurred.

### State recovery validation

The 28 October cleanup workflow produced the `encrypted-state-recovery` artifact
(ID `12770017148`), retaining encrypted snapshots for 90 days. Automatic Azure
hydration was not performed after the test infrastructure had been removed.
Restoration requires recreating the management and application backends first;
see [state recovery](state-recovery.md) for prerequisites and limits.
