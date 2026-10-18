# Engineering decisions and verification

This service emphasizes explicit invariants and reproducible checks. Start with
`api.py` for the request lifecycle, `admission.py` for shared accounting, and
`provider.py` for the transport boundary. The corresponding unit, integration,
and contract suites are under `app/tests/`. Run `make test`, `make lint`, and
`make policy`; run `npm test && npm run build` from `hosted-ui/` for the client.
The broader release command and its prerequisites are documented in the README.

## Decisions that affect correctness

| Decision | Reason and trade-off |
|---|---|
| Shared Table transaction for admission | Global and principal counters commit together with the reservation. ETags detect races across replicas. A shared partition makes atomicity straightforward but creates a throughput bottleneck. |
| Continuous rate state, dated budget rows | Crossing UTC midnight resets the daily allowance without replenishing rate burst capacity. Settlement uses the original reservation day. |
| Retain reservations for uncertain outcomes | A read timeout does not prove the provider did no work. Retention favors spend safety and may temporarily deny otherwise affordable requests. |
| Retry only proven pre-send failures | Avoids duplicate inference charges after a response, timeout, or reset. Bounded jitter and retries consume the original request deadline. |
| One deadline across body, admission, and provider | Slow storage cannot give provider dispatch a fresh full timeout. Lock acquisition and Table transport calls receive the remaining budget. Transport deadlines remain subject to SDK and network behavior. |
| Early structural rejection with independent application validation | APIM filters malformed chat input; the application owns model, role and token policy. A small schema avoids copying deployment-specific business rules into edge policy. Shared fixtures check the boundary; cloud enforcement awaits rollout. |
| Explicit cloud adapters | Local doubles make policy failures reproducible; Azure runtime does not silently fall back to memory, fixture authentication, or local credentials. AWS remains a documented skeleton. |

Azure was chosen for integrated Entra identity, managed identity, Key Vault,
private networking, transactional Table entities, and Monitor integration.
Front Door/WAF, APIM, and Container Apps exercise distinct edge, API-policy,
and runtime boundaries. Their added cost and operational complexity require
justification for a production workload; a smaller topology may suffice where
those boundaries are unnecessary. Portable interfaces do not establish cloud
feature parity.

## Tests that challenge the design

- **Competing writers:** synchronized requests compete for the final budget
  slot; exactly one reservation succeeds. Independent gateway instances share
  a store, and concurrent repeated settlement must adjust counters only once.
- **Time boundaries:** injected clocks test refill, clock rollback, midnight
  rate continuity, and settlement against the original budget day.
- **Slow dependencies:** controlled store delays test deadline exhaustion,
  bounded coordinator waiting, and progress for another principal. The
  provider receives only the remaining time after admission.
- **Provider uncertainty:** fake HTTP transports exercise partial failures,
  missing usage, billing faults, empty completions, and reasoning-only output.
  Assertions check safe error mapping, retained accounting, and absence of
  unsafe retries.
- **Security boundaries:** generated RS256 fixtures test issuer, audience,
  JWKS rotation and scope/role separation. A socket-blocking test checks that
  the local pipeline makes no outbound connection; sentinel values check
  that prompt content and secrets do not appear in telemetry.
- **Deployment contracts:** configuration tests check policy ordering, exact
  CORS origins, emitted dashboard dimensions, alert/runbook wiring, and Table
  endpoint normalization. Shared [request fixtures](../app/tests/fixtures/chat-structure-cases.json)
  are checked against the edge JSON Schema by `make configs` and against the
  application by `make test`. Deliberate business-rule failures must pass the
  structural schema and still fail application validation. These tests detect
  regressions in source; they do not replace effective cloud enforcement tests.

Some integration tests use real HTTP/ASGI and SDK-facing adapters with isolated
fake dependencies. They prove component interaction, not Azure transaction
semantics. The historical two-replica load run used a stub provider; it proves
neither live inference throughput nor every shared-budget case. Current cloud
observations and unproven checks are in [cloud acceptance status](cloud-acceptance-status.md).

## Working through provider and platform ambiguity

Model selection began with the provider catalog and explicit model IDs, rather
than inferring access from an API-key prefix. Bounded probes found throttled
free-model candidates and a working fixed model. A 429 response describes that
attempt; it does not prove a model is permanently unavailable. Prices and
availability require rechecking, and the provider model is configured behind a
stable application alias.

The intermittent chat investigation distinguished a successful provider HTTP
response from usable completion text. Reasoning could consume the small output
allowance and leave no visible answer. The adapter disables provider reasoning
for this chat use case and rejects empty or reasoning-only completions; the
regression tests preserve that behavior. The demo client now requests up to
512 output tokens. That is a configurable demo choice, not a language-model
capability claim or a guarantee against other upstream failures.

Azure Table transactions also exposed a protocol detail: the batch envelope
can report HTTP 202 while an inner conditional operation fails. The adapter
classifies the SDK error code as well as outer HTTP 409/412, with regression
tests for retryable conflicts. Request-wide deadlines and bounded lock waiting
were added after contention analysis. Local regression success and historical
cloud load success remain distinct claims.

## Production boundaries

Pipeline tests also check the boundary between orchestration and actual work:
an overall green run with a skipped cloud job is rejected. An empty Terraform
backend is established through authenticated Blob metadata, rather than by
ignoring a failed state command. Private-runner probes caught an unset home
directory in Azure's command environment; startup now supplies a clean user
environment. Failure cleanup is bound to its own lease and preserves execution
when an applying child cannot be confirmed stopped. These checks target recovery
and isolation, rather than relying only on successful provisioning.

The implementation is a bounded assessment service, with production-oriented
security and failure handling. It is not a complete production platform.
Before expanding traffic, add a measured in-flight concurrency cap and a safe
reservation retention/reconciliation process; measure hot-partition capacity
and define service objectives. The circuit breaker is process-local. Issued
bearer tokens are not immediately revoked, price-based reservations are not a
billing ledger, and multi-region recovery is not implemented. Effective egress
restriction, end-to-end diagnostic correlation, direct-origin enforcement, and
full cold recreation still need the cloud proof recorded in the status
document. These limits are part of the design review, not hidden by passing
unit tests.

For the edge/app validation split, schema lifecycle and deployment caveats,
see [edge content validation](architecture.md#edge-content-validation).
