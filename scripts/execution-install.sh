#!/bin/bash
# Non-secret VM bootstrap. Registration happens through protected Run Command.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl jq gh python3-venv libicu74 ca-certificates build-essential unzip zstd >/var/log/odyn-install.log 2>&1
python3 -m venv /opt/azure-cli
/opt/azure-cli/bin/pip install --disable-pip-version-check azure-cli==2.87.0 >>/var/log/odyn-install.log 2>&1
ln -sf /opt/azure-cli/bin/az /usr/local/bin/az
mkdir -p /opt/odyn-runner /opt/hostedtoolcache
curl -fsSL https://github.com/actions/runner/releases/download/v2.338.0/actions-runner-linux-x64-2.338.0.tar.gz -o /opt/runner.tar.gz
printf '%s  %s\n' af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32 /opt/runner.tar.gz | sha256sum -c -
tar -xzf /opt/runner.tar.gz -C /opt/odyn-runner
chown -R odynrunner:odynrunner /opt/odyn-runner /opt/hostedtoolcache
touch /opt/odyn-ready
