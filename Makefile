# Local Makefile — no deployment targets. No `apply`, no cloud writes.
# Every target FAILS (non-zero) when its tool, artifact, or assertion is missing.
#
# Python selection: project venv (3.13) by default — the ignored project environment contains
# the locked application and validation dependencies.
# Override deliberately, e.g. `make test PY=python3.13`.
# System `python3` may resolve to an older interpreter, so bare `python3`
# is never used by test, policy, or release targets.
SHELL := /bin/bash
PY ?= ./.venv/bin/python
export PYTHONPATH := app/src:app/tests
.PHONY: help test test-unit fmt lint tf-fmt tf-validate tflint checkov gitleaks trivy k6 policy py-check release build compile configs

py-check:
	@if ! $(PY) -c "import sys" >/dev/null 2>&1; then echo "FAIL: $(PY) missing — create the ignored 3.13 env first (see README toolchain)"; exit 2; fi
	@$(PY) -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" || (echo "FAIL: $(PY) is below the 3.13 floor"; exit 2)

help:
	@echo "Targets: test fmt lint tf-fmt tf-validate tflint checkov gitleaks trivy k6 policy compile build configs release"
	@echo "Local-only. No apply/deploy. Missing tools are reported, not installed."

test: test-unit
test-unit: py-check
	$(PY) -m unittest discover -s app/tests -v

fmt:
	@$(PY) -m ruff --version >/dev/null
	$(PY) -m ruff check app scripts && $(PY) -m ruff format --check app scripts

lint: fmt
	@$(PY) -m mypy --version >/dev/null
	$(PY) -m mypy --config-file app/pyproject.toml app/src

tf-fmt:
	@if ! command -v terraform >/dev/null; then echo "PENDING: terraform not installed; cannot run fmt"; exit 2; fi
	terraform fmt -check -recursive infra

tf-validate:
	@if ! command -v terraform >/dev/null; then echo "FAIL: terraform not installed"; exit 2; fi
	terraform -chdir=infra/modules/llm-gateway validate
	terraform -chdir=infra/envs/dev/azure validate
	terraform -chdir=infra/bootstrap validate
	terraform -chdir=infra/artifacts validate

tflint:
	@if ! command -v tflint >/dev/null; then echo "PENDING: tflint not installed"; exit 2; fi
	tflint --chdir=infra/modules/llm-gateway

checkov:
	./.venv-scan/bin/checkov -d infra --framework terraform --skip-download --quiet --compact

gitleaks:
	@if ! command -v gitleaks >/dev/null; then echo "PENDING: gitleaks not installed; config at .gitleaks.toml"; exit 2; fi
	$(PY) scripts/scan_repo.py

trivy:
	@if ! command -v trivy >/dev/null; then echo "FAIL: trivy not installed"; exit 2; fi
	@test -n "$(IMAGE)" -a -n "$(COLLECTOR_IMAGE)" || (echo "FAIL: supply IMAGE and COLLECTOR_IMAGE for existing local images"; exit 2)
	trivy image --image-src docker --exit-code 1 --severity HIGH,CRITICAL "$(IMAGE)"
	trivy image --image-src docker --exit-code 1 --severity HIGH,CRITICAL "$(COLLECTOR_IMAGE)"

k6:
	@if ! command -v k6 >/dev/null; then echo "FAIL: k6 not installed"; exit 2; fi
	@test -n "$$AUTH_TOKEN_A" -a -n "$$AUTH_TOKEN_B" || (echo "FAIL: export two local fixture tokens before running the loopback-only stub test"; exit 2)
	k6 run --no-usage-report loadtest/k6-limits.js

policy: py-check
	$(PY) scripts/policy-guard.py

release: py-check
	$(PY) scripts/release-check.py
build: py-check
	$(PY) -m build --no-isolation app
compile: py-check
	$(PY) -m compileall -q app/src app/tests scripts
configs:
	./.venv-scan/bin/python scripts/check-configs.py
