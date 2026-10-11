# Developer and operator scripts

Run scripts from the repository root. Read each command's `--help` and its
confirmation guards before use.

## Local engineering checks

- `make test` runs unit, integration and contract suites.
- `make fmt` and `make lint` run Ruff formatting/lint and mypy.
- `make policy` checks repository and Terraform invariants.
- `make configs` parses supported configuration and policy files.
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
