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
cat >/opt/odyn-runner-loop.sh <<'LOOP'
#!/bin/bash
set -euo pipefail
export RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
cd /opt/odyn-runner
mapfile -t registration_tokens < <(jq -er '.[] | select(type == "string" and length > 0)' <<<"$ODYN_REGISTRATION_TOKENS")
unset ODYN_REGISTRATION_TOKENS
if [ "${#registration_tokens[@]}" -lt 1 ]; then
  exit 1
fi
for registration_token in "${registration_tokens[@]}"; do
  if [ "$(date -u +%s)" -ge "$ODYN_DEADLINE" ]; then
    break
  fi
  # Keep registration credentials in this root-owned controller process.
  # They are passed only to the short config command and never into the runner
  # listener or workflow-job environment.
  runuser -u odynrunner -- env -i \
    HOME=/home/odynrunner USER=odynrunner LOGNAME=odynrunner \
    PATH=/usr/local/bin:/usr/bin:/bin \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    ./config.sh --unattended --ephemeral --disableupdate \
      --url "https://github.com/$ODYN_REPOSITORY" \
      --token "$registration_token" --name "$ODYN_RUNNER_NAME" \
      --labels odyn-azure,odyn-cleanup --work _work
  unset registration_token
  runuser -u odynrunner -- env -i \
    HOME=/home/odynrunner USER=odynrunner LOGNAME=odynrunner \
    PATH=/usr/local/bin:/usr/bin:/bin \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    ./run.sh
  rm -rf _work /home/odynrunner/.azure /home/odynrunner/.config/gh
done
LOOP
chmod 700 /opt/odyn-runner-loop.sh
# Run Command's protected token pool remains in a root-owned process and never
# appears in cloud-init data, VM files, or the unprivileged Actions runner.
nohup env -i \
  HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  ODYN_REPOSITORY="$ODYN_REPOSITORY" ODYN_DEADLINE="$ODYN_DEADLINE" \
  ODYN_RUNNER_NAME="$ODYN_RUNNER_NAME" \
  ODYN_REGISTRATION_TOKENS="$ODYN_REGISTRATION_TOKENS" \
  /opt/odyn-runner-loop.sh >/var/log/odyn-runner.log 2>&1 </dev/null &
