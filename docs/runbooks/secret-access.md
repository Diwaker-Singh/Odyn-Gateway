# Runbook: unexpected secret access

Treat unexpected secret reads, role assignments, firewall changes or secret
version changes as a security incident.

1. Identify the secret reference, caller identity, timestamp and related role
   or network-policy changes from platform audit records.
2. Never print, copy or place a secret value in chat, logs or incident notes.
   Rotate it through the approved secret-manager process.
3. Revoke unexpected access and review recent identity and network changes.
4. Scan source and operational outputs for accidental secret material.
5. Verify the intended runtime identity can resolve the configured secret
   reference after mitigation.

Keep a sanitized incident timeline. Use the organization's private security
escalation channel for identity and credential details.
