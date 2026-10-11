#!/usr/bin/env bash
# Set the OpenRouter provider key in Azure Key Vault out of band.
#
# This script is intentionally not called by Terraform, CI, tests, or Docker.
# It never prints the supplied value and uses a 0600 temporary file so Azure
# CLI receives it through --file rather than a shell-history command argument.
# Run only with an approved interactive cloud identity and the intended vault.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/set-provider-key.sh --vault <vault-name> [--name <secret-name>] [--placeholder]

Without --placeholder, the script securely prompts for a rotated, scoped,
spend-capped provider key. --placeholder writes a deliberately nonfunctional
marker only to validate Key Vault reference wiring; it must never be used with
live upstream mode.
EOF
}

vault_name=""
secret_name="openrouter-api-key"
placeholder=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault)
      vault_name="${2:-}"
      shift 2
      ;;
    --name)
      secret_name="${2:-}"
      shift 2
      ;;
    --placeholder)
      placeholder=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$vault_name" ]]; then
  echo "ERROR: --vault is required" >&2
  usage >&2
  exit 2
fi

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: Azure CLI is required; it will not be installed automatically" >&2
  exit 2
fi

if ! az account show --only-show-errors --output none; then
  echo "ERROR: approved interactive Azure login is required before setting a secret" >&2
  exit 2
fi

if [[ "$placeholder" == true ]]; then
  provider_key="DUMMY-NOT-FOR-LIVE-OPENROUTER-USE"
else
  read -r -s -p "OpenRouter provider key (input hidden): " provider_key
  printf '\n'
  if [[ -z "$provider_key" ]]; then
    echo "ERROR: empty provider key refused" >&2
    exit 2
  fi
fi

umask 077
tmp_file="$(mktemp "${TMPDIR:-/tmp}/odyn-gateway-provider-key.XXXXXX")"
cleanup() {
  unset provider_key
  rm -f "$tmp_file"
}
trap cleanup EXIT HUP INT TERM
printf '%s' "$provider_key" >"$tmp_file"
unset provider_key

az keyvault secret set \
  --vault-name "$vault_name" \
  --name "$secret_name" \
  --file "$tmp_file" \
  --only-show-errors \
  --output none

if [[ "$placeholder" == true ]]; then
  echo "Placeholder stored. Replace it with a rotated real key before enabling live upstream mode."
else
  echo "Provider key stored. The value was not printed."
fi
