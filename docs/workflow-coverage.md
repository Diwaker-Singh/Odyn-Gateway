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
| `management.py` | Secure ARM template, destination and configuration unit tests; manual management-state workflow; real isolated Azure storage creation passed. |
| `cleanup.py` | Encryption, approval expiry/source/run/identity binding, inventory drift, confirmation, durable recovery and ordered-deletion tests; manual protected cleanup workflow, real deletion not exercised. |
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
| Exact-source CI | [38948431452](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38948431452), passed for `bedebb9`. |
| Image publication | [38946401639](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38946401639), passed for `7bca5c1`; published and verified both images. |
| Foundation bootstrap | [38883057735](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38883057735), passed; saved plan applied and second plan clean. |
| Resource inventory | [38883061100](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38883061100), passed with Reader OIDC. |
| Public health | [38881528968](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38881528968), passed without valid chat/provider calls. |
| Registry provisioning | [38945392647](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38945392647), saved no-change plan applied and second plan clean. |
| Application Actions deployment | [38947234780](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38947234780), passed for `7bca5c1`; reviewed saved plan, clean second plan, revision readiness and public health/auth/CORS. |
| Management bootstrap | [38945385264](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38945385264), actual isolated storage creation passed. |
| Cleanup | Safeguard validation passed; disposable Azure execution failed safely at inventory; metadata-read permissions are now configured and a cloud retest is pending. No live application cleanup has run. |

The successful cloud runs refer to the exact source linked above, not subsequent
workflow edits. The first temporary runner was removed. The renewed independent-VNet test VM is now deallocated; disk/IP cleanup remains. Recreate approved private execution before future delivery. The existing application is retained. OIDC trust used exact
observed immutable owner/repository-ID subjects. The initial test found and fixed
missing runner tools, an OIDC subject mismatch, and missing registry metadata
Reader access; these were not application deployment tests.

Interactive human SSO, secret entry and administrative identity grants remain
intentional operator boundaries. Credential-free success does not establish
cloud provisioning, deletion or production qualification.
