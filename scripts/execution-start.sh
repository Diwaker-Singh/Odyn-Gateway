#!/bin/bash
# Azure managed Run Command supplies the token as a protected environment value.
set -euo pipefail
umask 077
for attempt in $(seq 1 600); do
  test -f /opt/odyn-ready && break
  sleep 1
done
test -f /opt/odyn-ready
test "$(date -u +%s)" -lt "$ODYN_DEADLINE"
printf '%s' "$ODYN_REGISTRATION_TOKEN" >/home/odynrunner/.registration-token
chown odynrunner:odynrunner /home/odynrunner/.registration-token
unset ODYN_REGISTRATION_TOKEN
cat >/opt/odyn-runner-loop.sh <<'LOOP'
#!/bin/bash
set -euo pipefail
export RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
cd /opt/odyn-runner
trap 'rm -f /home/odynrunner/.registration-token' EXIT
while [ "$(date -u +%s)" -lt "$ODYN_DEADLINE" ]; do
  ./config.sh --unattended --ephemeral --disableupdate --url "https://github.com/$ODYN_REPOSITORY" --token "$(cat /home/odynrunner/.registration-token)" --name "$ODYN_RUNNER_NAME" --labels odyn-azure,odyn-cleanup --work _work
  ./run.sh
  rm -rf _work /home/odynrunner/.azure /home/odynrunner/.config/gh
done
LOOP
chmod 700 /opt/odyn-runner-loop.sh
chown odynrunner:odynrunner /opt/odyn-runner-loop.sh
# Run Command's environment stays out of the public VM custom-data document.
nohup runuser -u odynrunner --preserve-environment -- /opt/odyn-runner-loop.sh >/var/log/odyn-runner.log 2>&1 </dev/null &
