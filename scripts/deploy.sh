#!/usr/bin/env bash
# ODYN_DEPLOY_APPROVED=1 and exact saved-plan review are required by deploy.py.
# ODYN_PLAN_APPROVED=1 gates plan separately. Never destroys; default is local.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${PY:-$ROOT/.venv/bin/python}" "$ROOT/scripts/deploy.py" "$@"
