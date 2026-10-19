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

Cloud delivery was exercised on 16–17 October 2026 (IST).

| Operation | Actual result |
|---|---|
| Exact-source CI | [Latest main CI](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/workflows/ci.yml?query=branch%3Amain); inspect the run source SHA and all 12 jobs, including image scans. |
| Private execution bootstrap | [39163453137](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39163453137), passed all four jobs for `19686c4`: validation, isolated runner creation, tool/credential-isolation probe and automatic retirement. |
| App-authenticated execution | On 19 October, [39210974646](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39210974646) passed creation and the isolated probe, then was cancelled before obsolete retirement code ran. Corrected retirement [39211831808](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39211831808) passed for `485d8ed`: no execution resources remained and the empty group's scoped Contributor assignment was retained. These are separate component results, not one fully passing lifecycle. |
| Lifecycle controller | [39163289856](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39163289856), validation-only dispatch passed; orchestration/skip/source/failure guards have local tests. Full topology recreation has not run. |
| Image publication | [38946401639](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38946401639), passed for `7bca5c1`; published and verified both images. |
| Foundation bootstrap | [38883057735](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38883057735), passed; saved plan applied and second plan clean. |
| Resource inventory | [38883061100](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38883061100), passed with Reader OIDC. |
| Public health | [38881528968](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38881528968), passed without valid chat/provider calls. |
| Registry provisioning | [38945392647](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38945392647), saved no-change plan applied and second plan clean. |
| Application Actions deployment | [38947234780](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38947234780), passed for `7bca5c1`; reviewed saved plan, clean second plan, revision readiness and public health/auth/CORS. |
| Management bootstrap | [38945385264](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38945385264), actual isolated storage creation passed. |
| Cleanup | [39158982129](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39158982129), passed for `3b7dcdd`: encrypted durable recovery, protected deletion and final absence checks on the isolated fixture. No live cleanup ran. |

The successful cloud runs refer to the exact source linked above, not subsequent
workflow edits. The temporary runner/network group and isolated test-management
group were removed after verification. On 19 October the owner requested
retirement of all billable gateway resources, with encrypted state preservation
and empty permission anchors retained. The static client is not proof of a
running API. Recreate approved private execution before future delivery.
OIDC trust used exact
observed immutable owner/repository-ID subjects. The initial test found and fixed
missing runner tools, an OIDC subject mismatch, and missing registry metadata
Reader access; these were not application deployment tests.

The 19 October disposable cold controller [39212823565](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39212823565)
did not pass its protected execution gate. Automatic approval review was
unavailable, the approved window expired, and the run was cancelled before
allocation. Full cold recreation remains unverified. Provisioning flags are
disabled while the requested cost-retirement work is completed. The final
read-only edge check passed health, HSTS, unauthenticated rejection and CORS;
it made no valid chat/provider request.

Interactive human SSO, secret entry and administrative identity grants remain
intentional operator boundaries. Credential-free success does not establish
cloud provisioning, deletion or production qualification.

### Cleanup test scope and failure learning

The fixture used three genuine Terraform states, empty dedicated application and
registry groups, and a real state-storage account. Its managed-app group was
already absent. This exercises missing-group handling and deletion/recovery,
not the duration or failure modes of tearing down APIM, Key Vault or a running
Container Apps environment. Full live teardown remains separately authorized.

The first attempts failed closed during inventory: group-scoped permissions
could not establish absence of a missing group. A separately approved custom
role adds only subscription-level resource-group metadata read. Delete rights
remain restricted to the target groups. With that permission the same cleanup
path passed, including absence checks after scoped role assignments disappeared.
Encrypted recovery was also verified privately before deletion; no plain state
or key was published.

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
