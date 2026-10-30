# Pipeline sufficiency validation roadmap

This runbook defines one controlled pass to show that the Odyn Gateway setup,
release, deployment, rollback and cleanup can be operated through GitHub Actions.
It is designed to avoid repeated exploratory runs. It does not authorize Azure
spend, resource changes, role assignments, or live provider calls; obtain a fresh
time and spend limit before starting any cloud-mutating stage. Azure alerts and
workflow timeouts are not hard spending caps.

## Known starting point

The latest recorded state says there is no current Azure Gateway deployment,
the Azure workflow switches are off, and no self-hosted runner is configured.
The 27–28 October disposable component runs exercised management-state setup,
foundation, release, deployment and cleanup. The recorded rollback workflow was
a no-op because the selected image already matched. The full cold-start
controller did not pass as one end-to-end run. The latest execution workflow
attempt on 29 October selected `action=stop` and failed after OIDC sign-in; it
was not a start or deployment attempt. Recheck all of these facts before acting;
historical run evidence is not a live inventory.

## Run order

### 1. Freeze the test candidate and scope

- Choose one source commit on `main`; require a clean working tree and passing
  exact-commit CI before any Azure action.
- Use a disposable environment and unique resource suffix. Do not point the
  test at the interview/demo resource groups.
- Agree one incremental Azure spend ceiling and a stop time. Include expected
  APIM/Front Door runtime, registry, state storage, logs and teardown. Set the
  planned end time early enough to finish cleanup.
- Record baseline resource groups, resource inventory and cost query. If cost
  attribution is delayed, record actual spend as unknown rather than zero.
- Keep all workflow switches off until the preflight below passes.

**Gate:** candidate commit, disposable target, budget ceiling and stop time are
written in the run record. Stop if any are missing or the target overlaps the
demo environment.

### 2. Run the no-write preflight

- Run CI and each workflow's `validation_only=true` path at the frozen commit.
- Check GitHub protected environments, approvals, OIDC subjects and each
  identity's effective Azure role scope. Confirm cleanup rights are restricted
  to the named disposable groups.
- Confirm state account, container, private DNS, runner subnet routing and
  firewall rules are compatible. From the actual runner network, resolve the
  storage endpoint and establish the required HTTPS path before Terraform init.
  A public hosted runner must not be used to reach a private-only backend.
- Confirm state containers are reachable using Entra authentication, leases can
  be acquired, and encrypted recovery has a destination outside the resources
  that cleanup will delete.
- Check the workflow concurrency group is idle. Dispatch one cloud mutation at
  a time; wait for completion before the next.

**Gate:** every required identity, network path, state backend and approval is
proven. A state-storage timeout or firewall denial blocks all later cloud
stages; fix the runner-to-storage path once, then rerun the preflight before
provisioning.

### 3. Start the temporary execution runner

- Enable only the execution switch and configure the protected execution
  environment for the disposable target.
- Run the execution workflow's isolated `test` action. Verify the runner joins
  the repository with the expected labels, executes the private probe, receives
  no inherited credentials, and is retired automatically.
- Start a bounded runner lease for the lifecycle run only after the probe
  succeeds. Ensure the lease covers setup, release, deployment, validation and
  retirement, with a safety margin before the stop time.

**Gate:** runner is healthy, network-connected to state storage and enrolled
only for trusted repository workflows. Stop if the private probe or state
connectivity fails.

### 4. Bootstrap management and foundation

- Run management-state bootstrap, then verify default-deny networking, TLS,
  versioning/retention, Entra-only access and the intended state role scopes.
- Run foundation bootstrap for the disposable stack and registry.
- Require a second Terraform plan to show no unexpected changes. Save plan
  summaries, source fingerprints and state lineage metadata; never publish raw
  Terraform state or plan secrets.

**Gate:** management backend is reachable and recoverable; foundation resources
match the reviewed disposable scope; clean second plan passes. Stop on drift,
unexpected creates/replacements or missing recovery evidence.

### 5. Release one intentional source change

- Make the requested small, reviewable token-limit change and add/update its
  focused boundary tests. Do not increase the service's global admission or
  spend limits as a side effect.
- Commit the release candidate only after local validation and exact-commit CI
  pass. The release workflow must build, scan and smoke-test both images, bind
  them to the exact source commit, publish by digest and emit a verified-release
  artifact.
- Confirm the recorded remote digest maps to the scanned image. Treat registry
  tags as mutable; deployment input must use the verified digest or the exact
  publication run artifact.

**Gate:** one successful publication for the frozen source revision, with image
scan results and source-to-digest provenance. A validation-only release does
not satisfy this gate.

### 6. Deploy and verify the new revision

- Dispatch deployment using the successful publication run ID and disposable
  profile. Require the saved-plan check and protected environment approval.
- Apply only the plan produced for the frozen commit and target. Require a clean
  follow-up plan and verify the intended Container App revision and image
  digests.
- Run bounded public checks: Front Door health, unauthenticated rejection,
  CORS preflight, and authenticated chat. Use the provider mode intended for
  this acceptance run; cap request count/output tokens and make no test call if
  the key or spend limit is not explicitly available.
- Verify rate limiting, request-wide deadline, telemetry correlation and the
  selected alert condition using controlled, low-volume stimuli. Keep provider
  and stub claims separate.

**Gate:** the deployed revision is ready and all chosen acceptance checks pass.
Record the exact request IDs, revision, digest, HTTP outcomes and timestamps;
never record access tokens or the provider key.

### 7. Prove rollback with a real change

- Ensure the deployed image differs from the known prior release. Dispatch the
  rollback workflow using the previous verified publication artifact/digest.
- Require a plan with the expected in-place image change, then verify the new
  revision uses the prior digest and passes health/auth checks.
- Do not count a no-op rollback plan as rollback evidence.

**Gate:** before/after image digests differ as expected and the prior revision is
healthy after rollback. If the plan is a no-op or unexpected, stop and preserve
the evidence.

### 8. Leave the validated deployment for user testing

- After any rollback test, roll forward to the intended final release and
  repeat health/auth checks. Confirm the final revision and image digests.
- Keep the deployed test environment available for the user to test. Do not run
  cleanup, delete groups, or retire resources needed by the service until the
  user confirms testing is complete.
- Retire only the temporary execution runner and temporary grants when they are
  no longer needed; confirm this does not interrupt the deployed application.
- Provide the endpoint, the safe test steps, the exact deployed revision, and
  the agreed stop time. The user should not send tokens or secrets in chat.

**Gate:** the latest intended revision is healthy and remains deployed; cleanup
is explicitly deferred until the user finishes testing.

### 9. Run cleanup after user testing

The cleanup pipeline already passed on the separate disposable `auto27` stack.
For this new environment, after the user says testing is complete:

- Capture encrypted backups of every Terraform state needed for recovery before
  deletion. Confirm the backup is readable and bound to the expected workspace,
  lineage and resource inventory without exposing plaintext state.
- Dispatch the protected cleanup workflow against the exact deployed test target.
  Review its inventory and deletion scope through the configured GitHub
  environment gate; do not substitute another environment.
- Verify all target groups and billable resources are absent, temporary network
  rules and role assignments are removed, and the execution runner is retired.
  Re-run read-only inventory and cost queries; report delayed cost data as
  unknown until it settles.

**Gate:** cleanup run succeeded, target absence is independently verified, and
state recovery artifact is retained. Stop if ownership is unexpected or recovery
fails; do not delete around missing state.

## Evidence bundle and completion definition

Store a sanitized run summary outside secrets and raw state. Include:

- source commit and exact CI run;
- workflow run IDs, target profile and start/end times;
- preflight results for OIDC scopes, runner-to-state connectivity and backend
  lease/recovery;
- bootstrap and deployment plan summaries, including the clean second plan;
- release artifact identity, source fingerprint, local scan result and remote
  image digests;
- before/after deployment and rollback revisions/digests;
- bounded health/auth/chat/load results with explicit stub/live mode;
- cleanup state-backup identity, deleted-group verification, runner/role/network
  cleanup and current cost-query limitations.

Call pipeline sufficiency **validated** for this cycle after cold bootstrap,
release, deployment and a real rollback all pass on the disposable profile at
the same reviewed source revision and the final release is healthy. Cleanup has
prior disposable evidence and will be re-run against this deployment only after
the user's test. If a stage is skipped or uses a no-op, report it as unverified.
