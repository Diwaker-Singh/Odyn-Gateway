# Runbook: authentication failures

1. Separate 401 responses (missing/invalid authentication) from 403 responses
   (valid identity without the required permission).
2. Compare the time window with identity-provider, key-rotation and gateway
   configuration changes.
3. Inspect aggregate status and redacted diagnostics only. Never record raw
   access tokens or personal token claims.
4. Verify issuer, audience, signing-key refresh and the expected delegated
   scope or application role. Do not weaken token validation to restore access.
5. After mitigation, test one valid human flow and one valid workload flow,
   then confirm invalid tokens still fail closed.
