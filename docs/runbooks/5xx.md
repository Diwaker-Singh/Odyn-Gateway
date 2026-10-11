# Runbook: gateway errors and latency

1. Identify the affected route, region and running application revision.
2. Correlate gateway request/dependency telemetry using request and trace IDs.
   Do not include prompts, responses, bearer tokens or credentials in incident
   notes.
3. Separate gateway 502/503/504 responses, shared-store errors, upstream
   failures and readiness/revision failures.
4. Pause synthetic traffic when the authoritative store is unhealthy. Do not
   automatically replay a provider request if the provider may already have
   processed it.
5. For a confirmed bad revision, roll back to a known-good image digest using
   the deployment procedure. Verify health and a bounded request after
   mitigation.
