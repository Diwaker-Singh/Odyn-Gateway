#!/bin/bash
set -euo pipefail
umask 077
# Stop the registration loop before removing its current registration.
pkill -u odynrunner -f odyn-runner-loop.sh || true
pkill -u odynrunner -f Runner.Listener || true
if test -f /opt/odyn-runner/.runner; then
  cd /opt/odyn-runner
  runuser -u odynrunner -- ./config.sh remove --unattended --token "$ODYN_REMOVAL_TOKEN" >/var/log/odyn-remove.log 2>&1
fi
rm -f /home/odynrunner/.registration-token
