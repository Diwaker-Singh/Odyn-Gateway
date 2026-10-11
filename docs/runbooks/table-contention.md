# Runbook: shared-store contention

1. Reduce or stop load while investigating. Do not rerun a failing stress test
   against a live provider.
2. Compare aggregate response counts with gateway admission-conflict events and
   storage transaction diagnostics. Inspect transaction sub-operation outcomes,
   not only the outer batch response.
3. Confirm active image digest, replica count, request deadlines and shared
   store health. A process-local coordinator can reduce same-process stampedes;
   the store remains the cross-replica authority.
4. Keep admission fail-closed. Do not switch to in-memory state or retry a
   provider request whose dispatch status is ambiguous.
5. Recovery requires intentional throttling to return 429 with valid
   `Retry-After`, storage-induced 503s to stop, and reservations to remain
   atomic under concurrent writers.
