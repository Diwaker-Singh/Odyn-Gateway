# Pipeline setup, deployment, recovery and cleanup

## Safety and current status

Cloud workflows are manual, main-only, disabled until configured, and default to
`validation_only=true`. Turning validation off is necessary but insufficient:
required environments, OIDC roles, network access and repository enable flags
must also exist. Never use a public pull-request job on a private Azure runner.
A public demo with Developer APIM is not a production deployment.

The current runtime topology is unchanged. See [workflow execution status](workflow-coverage.md#execution-status--16-october-2026)
for actual tested operations versus local-only coverage. New management and
cleanup workflows have **not been executed against Azure**. Cleanup validation
does not delete the running service. Budget alerts and workflow timeouts are
not hard Azure spending caps.

Cloud-mutating workflows share one concurrency group. Dispatch them in order,
waiting for completion: GitHub retains only one pending run per concurrency
group, and a newer pending run cancels an older pending run even when active-run
cancellation is disabled. Validation-only runs have unique concurrency groups. The explicitly isolated disposable cleanup target has its own queue and test-only identity.

## 1. Administrative trust and management state

An administrator must initially configure GitHub OIDC trust and protected
GitHub environments. A workflow cannot securely grant its own initial Azure
permissions. Use exact observed federated subjects; see [delivery](delivery.md).
No Azure client passwords, subscription Owner grants or provider keys belong
in repository source or GitHub workflow inputs.

Create protected environment `azure-management`, main-only, with a dedicated
OIDC `AZURE_CLIENT_ID`. Give this identity resource-group creation permission
only where necessary, management-group Contributor and authorization to assign
Blob Data Contributor on the management account. For a pre-created group,
avoid subscription-wide Contributor. A real production setup should use a
reviewed custom role and independent approval.

Configure repository variables `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, and
`AZURE_MANAGEMENT_ENABLED=true` only after review. In that environment set
secret `MANAGEMENT_CONFIG_JSON`, for example:

```json
{
  "resource_group": "rg-odyn-gateway-automation-state",
  "storage_account": "YOURGLOBALLYUNIQUENAME",
  "container": "management",
  "location": "centralindia",
  "subnet_ids": ["/subscriptions/SUBSCRIPTION/resourceGroups/RUNNER_GROUP/providers/Microsoft.Network/virtualNetworks/RUNNER_VNET/subnets/PRIVATE_RUNNER"],
  "principal_ids": ["FOUNDATION_AND_RECOVERY_PRINCIPAL_OBJECT_ID"]
}
```

Replace every placeholder. Principal IDs are object IDs, not app/client IDs.
The private runner subnet must already exist, with a Microsoft.Storage service
endpoint and suitable network/DNS access. Keep it in an **independent management
VNet/group**, outside every application cleanup target. Runner provisioning and
administrative federation setup remain deliberate operator responsibilities;
these workflows do not provision their own privileged execution machines.

Run **Bootstrap automation state** (`management.yml`) with validation off. It
uses ARM, so it needs no Terraform backend. The source-defined deployment
creates Entra-only, TLS-only, default-deny storage with explicit subnets,
versioning and seven-day Blob/container retention, plus scoped state roles.
It uses incremental deployment and never deletes state blobs. Existing groups
must have `managed_by=odyn-management-pipeline`; a differently owned existing
group requires explicit administrative inspection/adoption, not silent takeover.
The earlier operator-created test account is not automatically adopted.

Rerunning this step recreates storage, **not lost state**. If managed resources
still exist, restore current verified state snapshots and check lineage/resource
addresses before foundation planning. An empty backend must never be used as a
shortcut to adopt an existing deployment.

## 2. Foundation and application configuration

Configure scoped identities, private runners, protected environments and secrets
as listed in [foundation setup](foundation-deployment.md) and [delivery setup](delivery.md).
Use separate component keys in the management container. The application state
account is distinct from management storage and is created by foundation bootstrap.

The normal runner label is `odyn-azure`. Install the documented tools, including
Azure CLI, GitHub CLI, git, unzip and zstd; setup actions supply pinned Python
and Terraform. Use ephemeral runners and recover any emergency state before
retiring them. Never leave an unused VM running merely to wait for dispatch.

Wait for successful push CI on the exact main commit, then run:

```sh
gh workflow run infrastructure.yml -f validation_only=false -f component=bootstrap
gh workflow run infrastructure.yml -f validation_only=false -f component=registry
```

Run each to completion and review/approve its saved plan before starting the
next. Foundation requires a clean second plan. Configure application private
variables/backend after bootstrap, and keep secret values in Key Vault. Human
SSO consent and hidden provider-key entry are intentionally not unattended jobs.

## 3. Publish and deploy

```sh
gh workflow run release.yml -f validation_only=false
# Wait for successful publication, then replace RELEASE_RUN_ID:
gh workflow run deploy.yml -f validation_only=false -f release_run_id=RELEASE_RUN_ID
```

Publication and deployment must use the **same main commit**. Publication scans
and verifies both images and records remote manifest digests. Deployment binds
the saved plan to source, identity, destination and private inputs, then applies
that plan after approval. It requires a clean second plan, revision readiness
and safe public health/auth/CORS checks. These checks make no valid provider
request. Human/machine authenticated inference, load and alert acceptance are
separate bounded demonstrations; see [cloud acceptance status](cloud-acceptance-status.md).

Pages deploys independently and takes its client settings from configuration.
Changing the public API URL or Entra redirect origins needs coordinated config
and exact-origin CORS updates. See [hosted UI](hosted-chat-ui.md).

## 4. Protected cleanup — only when the demo can stop

**Cleanup deletes every resource in the four dedicated groups**, including any
manual additions: application, managed Container Apps group, registry and
application state storage. It uses resource-group deletion, not Terraform
destroy. Review all resource IDs privately in Azure; address/count summaries do
not constitute a semantic review. It does not remove unrelated groups, Entra
applications, provider registrations, Pages, management state or external runners.

Create protected main-only environments `azure-cleanup-plan` and `azure-cleanup`:

- Same dedicated OIDC client ID in both; exact separate federation subjects.
- A custom role scoped to the four groups with `*/read` and
  `Microsoft.Resources/subscriptions/resourceGroups/delete` only; no general
  create/update or IAM/lock-removal rights. Azure authorizes group deletion
  without individual contained-resource delete grants; see [Microsoft's deletion
  model](https://learn.microsoft.com/azure/azure-resource-manager/management/delete-resource-group).
  No automatic role-granting or lock removal is performed.
- Blob Data Reader on application state; Blob Data Contributor on management
  storage for snapshot recovery/upload.
- In `azure-cleanup-plan`, secrets `TF_BACKEND_JSON`, `BOOTSTRAP_BACKEND_JSON`,
  `REGISTRY_BACKEND_JSON`, `PLAN_ENCRYPTION_KEY` from the configured backends.
- In `azure-cleanup`, the same `PLAN_ENCRYPTION_KEY` and client identity.
- Repo variables `AZURE_ENVIRONMENT_SUFFIX`, `BOOTSTRAP_RESOURCE_GROUP`,
  subscription/tenant; set `AZURE_CLEANUP_ENABLED=true` after inspection.

Use runner label `odyn-cleanup` with private state connectivity, **outside the
application VNet and deletion groups**. A runner within the application VNet
could prevent deletion or lose access midway. Do not relabel the earlier
application-VNet test VM as a cleanup runner.

For a disposable integration test, choose `target=disposable`, configure
`CLEANUP_TEST_ENVIRONMENT` and `CLEANUP_TEST_STATE_GROUP`, and use separate
`azure-cleanup-test-plan` / `azure-cleanup-test` environments with test-only
identities, state secrets and approvals. This does not redirect the deployment
secrets or application identity. The default target is `deployment`.

First run validation only. When ready for actual removal:

```sh
gh workflow run cleanup.yml -f validation_only=false \
  -f 'confirmation=delete YOUR_SUBSCRIPTION_ID rg-odyn-gateway-YOUR_ENVIRONMENT'
```

The snapshot job verifies subscription/identity/ownership and recovers all three
Terraform states. Missing/unreachable state stops cleanup, including partial
setup: recover such cases through the operator procedure. It encrypts the
snapshot and uploads it to a unique `recovery/RUN_ID/SHA256.enc` blob in retained
management storage **before** the deletion gate. The GitHub copy expires after
one day; durable recovery is the storage copy. Keep its encryption key separately
and securely; rotating the GitHub key does not reencrypt old snapshots.

Review the snapshot summary and Azure inventory, then approve `azure-cleanup`
within one hour. Apply verifies the same source/run/identity and inventory,
requires the exact confirmation, and removes groups in order. A mismatch,
changed inventory, ownership failure, lock, permission error or timeout stops
processing; later groups are retained. Verify absence and inspect billing after
completion. Already-absent groups are skipped, but an orphan managed group
requires operator investigation when its ownership can no longer be established.

## 5. Recreate after cleanup

Deleting a resource group also removes its scoped role assignments. An
administrator must reestablish the required creation/deployment permissions
before these pipelines can recreate that group. Their initial trust and
permissions must be anchored outside the deleted environment; a workflow cannot
grant itself access after its own scope disappears. The current demo identities
are scoped to existing groups and require this administrative step after full
cleanup. Do not compensate by granting subscription Owner to normal delivery.


Retained foundation state will initially describe deleted resources. Preserve
it, run a fresh foundation plan and review Terraform's refresh/recreation rather
than reusing an old saved plan. Bootstrap recreates application-state storage;
the deleted application's backend is empty only when all its resources really
are absent. Confirm absence before deploying. Soft-deleted Key Vault names,
purge protection and globally unique names can block immediate name reuse;
prefer a fresh environment suffix and update state keys, client URL, roles and
secrets deliberately. Provider secrets must be entered again when the original
vault is gone. If any resources survived, recover state/import before apply.

## 6. Remaining costs and the management boundary

Application cleanup does not guarantee zero further charges. Retained management
storage, snapshot versions/retention, external runners, unrelated resources and
billing latency must be reviewed. Stop/delete unused runner VMs, disks and IPs.
The earlier temporary test runner has been retired; it is not a persistent runner
service.

Management storage is deliberately outside the cleanup job's deletion scope.
To remove it too, first export and verify the latest foundation states and all
needed recovery blobs to independent private storage, retain encryption keys,
disable foundation/delivery/cleanup enable flags, then have an administrator
remove only that group. Do not destroy the sole state copy to save a small
storage charge. A future run of `management.yml` can recreate it, followed by
verified state restoration before managing surviving resources. Do not confuse
recreating an empty container with recovering an environment.
