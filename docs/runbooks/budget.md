# Runbook: accounting and shared-store anomalies

1. Correlate safe request IDs and trace IDs; inspect aggregate reservation,
   usage and store events. Never log prompts, credentials or bearer tokens.
2. Keep admission fail-closed while the authoritative store is unavailable. Do
   not fall back to process-local accounting.
3. Preserve conservative reservations when provider usage is unknown. Do not
   release a reservation or replay an ambiguous paid request automatically.
4. Check model pricing configuration, request caps, transaction conflicts and
   store latency. Pause live traffic if accounting correctness is uncertain.
5. Reconcile provider usage through approved private tooling before correcting
   an account. Verify recovery with controlled stub requests before resuming
   live traffic.
