# Developer and operator scripts

Run scripts from the repository root. Read each command's `--help` and its
confirmation guards before use.

## Local engineering checks

- `make test` runs unit, integration and contract suites.
- `make fmt` and `make lint` run Ruff formatting/lint and mypy.
- `make policy` checks repository and Terraform invariants.
- `make configs` parses supported configuration and policy files, validates
  the edge JSON Schema, and checks shared request fixtures using the locked
  scan environment. It does not execute the APIM policy engine.
- `make release` runs the local build and static validation suite; it makes no
  cloud calls. Local reports are placed under ignored `.artifacts/`.
- `scripts/scan_repo.py` checks Git source files and available history
  for secrets. Keep private local configuration ignored.

## Identity and local demo

`sso-demo.py` runs the configured human PKCE or machine certificate flow.
Token output is written atomically to mode-0600 files under ignored
`.secrets/sso/`; tokens are never printed. See [identity setup](../docs/identity-setup.md)
and [local chat demo](../docs/local-chat-ui.md).

`local_chat_ui.py` serves the loopback browser UI. Keep it on localhost; do not
expose it to the network or host it as a production frontend.

## Deployment operations

`deploy.sh` and `deploy.py` default to read-only preflight. Planning and apply
require explicit operator gates, a saved-plan hash, and destination/principal
checks. Use a dedicated, least-privileged deployment identity. Private backend
configuration, state, plans, receipts and credentials belong under ignored
`.secrets/`.

`set-provider-key.sh` accepts hidden interactive input and writes the value to
the selected secret manager. Never pass a key as an argument, Terraform
variable or environment value that may be logged.

The local chat UI sends bounded requests to the configured gateway. It can
incur provider charges; use only with a valid token, active model configuration
and an explicitly approved spend limit. Local unit/integration tests use a
stub or fake transport and do not call the provider.

## Teardown

`teardown.py` defaults to read-only inventory. Use its explicit execution flag
and typed confirmation only when ready to remove the dedicated deployment.
See the [teardown guide](../docs/teardown.md) for prerequisites, state recovery,
deletion order and cost verification.

## Automated delivery

`delivery.py` supports the separate release/deploy workflows: image provenance,
verified digest references, and encrypted saved-plan transfer. It does not use
the interactive driver’s Owner-role requirement. See [delivery setup](../docs/delivery.md)
for scoped OIDC identities, protected environments and private runner setup.

The manual foundation workflow uses `delivery.py prepare-infrastructure` for
bootstrap and registry roots, with separate durable management state. See
[foundation provisioning](../docs/foundation-deployment.md) before configuring it.

`test-local-load.py` starts the loopback stub, creates short-lived fixture tokens,
runs bounded k6 checks and cleans up its child server. `cloud-smoke.py` checks
public edge health, HSTS, authentication rejection and exact-origin CORS without
sending a valid chat. See [workflow coverage](../docs/workflow-coverage.md) for
Actions execution and the boundaries of interactive scripts.

`management.py` bootstraps durable automation storage through ARM without an
existing Terraform backend. `cleanup.py` is the protected Actions counterpart
to interactive teardown: it saves encrypted state to retained management
storage, binds deletion to reviewed inventory and the same source/run/identity,
and requires typed confirmation plus an environment approval. See the
[pipeline lifecycle guide](../docs/pipeline-lifecycle.md). These commands can
create costs or delete resources when their explicit execution modes are used.
