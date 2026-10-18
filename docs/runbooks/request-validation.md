# Runbook: request validation failures

The edge content/schema policy was deployed through Actions on 17 October 2026
(IST). Local schema tests and deployment readiness passed; authenticated malformed
request replay remains a separate cloud acceptance check. Retain application
validation in every environment.

| Response | Intended meaning | First check |
|---|---|---|
| 400 `bad_request` | APIM rejected malformed JSON or structural schema; the app also returns 400 for malformed JSON | Verify payload structure and the deployed APIM schema/policy. |
| 413 `body_too_large` | Edge size check or application streamed-body cap rejected input | Check the configured byte limit; do not increase it automatically. |
| 415 `unsupported_media_type` | Missing or unsupported content type | Send `application/json`; charset parameters and case variations are supported. |
| 422 `unprocessable` | Application rejected request structure or business rules | Check model alias, supported roles and token ceiling. |

The new APIM schema checks required field types and recognized properties; it
has no model allowlist, role enum, or deployment-specific token maximum.
Application validation remains authoritative. Structural failures can therefore
return 400 at the edge and 422 when calling the application directly.

1. Record the request ID, status, time, deployment revision and policy version.
   Correlate APIM and application diagnostics without copying prompts, tokens,
   credentials, raw bodies or verbose validation details.
2. Determine whether APIM forwarded the request. An application log alone cannot
   prove early rejection; use available gateway diagnostics and backend traffic
   correlation. If those are unavailable, keep that proof unverified.
3. For an unexpected 400 spike, verify the service schema ID
   `chat-request-structure`, its current document, and the policy dependency.
   Missing/broken schemas can also reject requests. Compare source against the
   approved deployment; do not assume all 400s are caller errors.
4. Check that origin/JWT checks and throttling still precede content validation,
   and that only operation `post-chat` runs body validation. GET readiness and
   CORS preflight must work without a chat body.
5. For a confirmed bad policy/schema revision, use a reviewed saved plan to
   restore a compatible schema and policy together. Do not bypass validation
   or replay live provider requests automatically.

## Bounded post-deployment checks

Use a valid authorized test identity, synthetic text, and the normal Front Door
path. These are manual acceptance checks, not evidence already obtained:

- Check exact-origin CORS preflight and authenticated readiness without bodies.
- Submit malformed JSON, array/null roots, missing fields, wrong types,
  boolean/string token counts, and extra root/message properties. Expect a
  generic 400 envelope and request ID, without schema details or input echoes.
- Send missing/wrong content types and then JSON with charset/case variations.
  The former should return 415; the latter should reach structural validation.
- Test input below, at and above the byte limit, including chunked bodies and
  an untrusted or absent declared length. Confirm application enforcement too.
- Send a structurally valid request with a deliberately unknown model alias:
  it should reach the application and return 422 without an OpenRouter call.
- Use stub mode for a valid 200 request and validation-throughput testing.
  Successful live requests can incur charges and need their own bounded scope.
- Correlate rejected requests with diagnostics to establish no backend or
  provider dispatch, rather than inferring it from the HTTP status alone.

Local equivalents: `make test` checks application and policy contracts;
`make configs` validates the JSON Schema and shared fixture matrix. Neither
command emulates Azure's policy engine or proves its effective configuration.

References: [validate-content policy](https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy),
[APIM error handling](https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies).
