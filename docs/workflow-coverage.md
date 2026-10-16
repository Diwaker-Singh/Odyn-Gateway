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
| `teardown.py` | Unit tests mock cloud calls and deletion/recovery failures. Operational Actions offers read-only inventory only; actual deletion remains a separately confirmed operator action. |
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

## Current setup boundary

Inspection on 16 October 2026 found only the existing Pages environment and
public client variables. There were no registered private runners or delivery
secrets/variables, nor dedicated Odyn automation applications. Actual
publication/foundation/apply needs scoped OIDC identities, protected environment
rules, private ephemeral Linux runners and persistent management state first.
The workflows do not create their own administrative trust or silently grant
permissions. Existing operator-managed foundation state must be migrated before
using these jobs against the assessment resources.

The earlier deployment spending window has expired. New runner/state resources,
image publication and deployment tests need a renewed spending/runtime bound.
Any Actions execution results should distinguish helper validation, public-edge
observations, publication and infrastructure changes.
