# Odyn Gateway

Odyn Gateway is a Python API service that authenticates callers, applies shared request and spend controls, and forwards bounded chat requests to a configured model provider. It uses FastAPI at the HTTP boundary and keeps authorization, admission, provider handling, and accounting in testable gateway components. Azure is the implemented cloud adapter; the AWS directory preserves the Terraform interface as a documented skeleton.

## Architecture

```mermaid
flowchart LR
  Client[Human or service client] --> IdP[OpenID Connect identity provider]
  IdP -->|signed access token| Edge[Optional API edge]
  Edge --> API[FastAPI gateway]
  API --> Auth[JWT and permission validation]
  Auth --> Admission[Shared atomic rate and budget admission]
  Admission --> Store[Azure Table Storage]
  Admission --> Provider[Provider adapter]
  Provider --> Stub[Deterministic local stub]
  Provider --> LLM[Configured HTTPS model API]
  API --> Telemetry[OpenTelemetry]
  Telemetry --> Collector[Local collector]
  Collector --> Monitor[Azure Monitor / Application Insights]
  Secrets[Key Vault via managed identity] --> Provider
```

Azure is the primary adapter because Entra ID, managed identity, Key Vault,
private networking, Table Storage and Azure Monitor fit the service's identity
and operational requirements. The provider interface keeps application policy
independent of Azure-specific storage and credentials. The AWS adapter is an
interface skeleton; it does not provision AWS resources or claim feature
parity.

Key design choices:

- RS256 JWT validation with trusted issuer, audience, time claims and bounded
  JWKS refresh; delegated scopes and application roles are handled separately.
- Continuous per-principal and global token buckets, plus daily token/spend
  budgets. Shared-store transactions reserve capacity atomically across
  replicas; store errors fail closed.
- Conservative usage settlement: an ambiguous upstream result retains its
  reservation, and retries are limited to failures proven to occur before a
  request is sent.
- Fixed HTTPS provider destination, bounded request/response sizes and
  deadlines, redirects disabled, and safe error/telemetry fields.
- A deterministic stub is the default. Live provider mode requires explicit
  configuration and a secret supplied by the deployment secret store.

See [architecture](docs/architecture.md), [threat model](docs/threat-model.md),
[provider portability](docs/provider-portability.md), and the [operational
runbooks](docs/runbooks/).

## Verification status

The current Azure deployment is live and the latest recorded health check found
revision 7 healthy with two replicas. Human and machine Entra sign-in have both
been demonstrated, and a bounded live OpenRouter request returned HTTP 200.
One controlled authentication alert reached Azure Monitor and its action group;
one two-replica load run passed in stub mode. These checks demonstrate working
paths, not full cloud acceptance. The remaining checks and their limits are in
[cloud acceptance status](docs/cloud-acceptance-status.md).

The latest local validation run recorded 222 passing gateway tests, 9/9 policy
checks, valid Terraform configuration in all 4 roots, and 4 passing hosted UI
configuration tests. The hosted UI dependency audit found 0 vulnerabilities.
GitHub Pages build and deployment passed for commit
`7c17b0498cdbd369a8a2162da2ab9e6316f1a860`: [workflow run
38133174589](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/38133174589).
The earlier image-inclusive release report recorded 21/21 checks. Cloud and
local evidence artifacts are kept outside the public source tree; the status
document labels what was observed and what is still unproven. Record a new demo
video after the authenticated hosted chat smoke test.

## Local development

Requirements: Python 3.13, Docker for image checks, and Terraform 1.9.8 for
infrastructure checks. Create an isolated environment and install the hashed
application dependencies:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r app/requirements.lock
make test
make fmt
make lint
make policy
make compile
make build
make configs
make release
```

`make release` runs local tests, formatting, types, package build, Terraform
validation, policy/configuration checks and repository secret scanning. It does
not contact Azure. Reports and local-only outputs are written under the ignored
`.artifacts/` directory. GitHub Actions runs the same core checks and builds and
scans both runtime images.

For Terraform validation, initialize each root with `-backend=false` and
`-lockfile=readonly` before running `terraform validate`. Never check in state,
plans, real variable files, credentials, or local reports.

## Chat demo

The configurable static browser UI is hosted at
https://diwaker-singh.github.io/Odyn-Gateway/ and uses the configured human
Entra client to send bounded requests to the gateway. A local Python proxy UI
is also available for development; start it with:

```sh
.venv/bin/python scripts/local_chat_ui.py
```

Configure the client using the steps in [identity setup](docs/identity-setup.md)
and follow the [local demo guide](docs/local-chat-ui.md). It binds to
`127.0.0.1:8400`; the access token remains in the local Python process. The
hosted SPA configuration, local preview, and security boundaries are described
in the [hosted chat UI guide](docs/hosted-chat-ui.md). The Entra redirect and
exact-origin APIM preflight are configured; the authenticated browser-to-chat
path should be smoke-tested before recording a new end-to-end demo. Hosting the
static files does not expose the OpenRouter key.

## Deployment and secrets

Terraform environments and operator commands are documented in
[`infra/envs/dev/README.md`](infra/envs/dev/README.md),
[`infra/bootstrap/README.md`](infra/bootstrap/README.md), and
[`scripts/README.md`](scripts/README.md). The deployment driver defaults to
read-only preflight and requires an explicitly reviewed saved plan for changes.
Use a least-privileged deployment identity, private state storage, and a
separate runtime managed identity.

The provider key is never a Terraform value or application setting. Store it in
the platform secret manager and configure the runtime to resolve its secret
reference through managed identity. The hidden-input helper
`scripts/set-provider-key.sh` avoids echoing the value. Do not put secrets in
source, command-line arguments, logs, screenshots, or chat.

## Repository layout

```text
app/src/gateway/       gateway domain, HTTP adapter and cloud/provider adapters
app/tests/             unit, integration and contract tests
infra/modules/         shared Terraform contract and cloud adapters
infra/envs/dev/        example Azure environment composition
infra/bootstrap/       remote-state bootstrap configuration
infra/telemetry/       collector image/configuration and workbook
scripts/               local checks, demo and guarded operator tooling
loadtest/              bounded local load scenarios
local-ui/              browser UI assets
docs/                  architecture, security, setup and runbooks
```

The service is designed for bounded workloads. Capacity, shared
partition throughput, provider availability, per-process breaker state, token
revocation latency, cloud egress enforcement, and production service-level
objectives require environment-specific engineering and operational validation.
