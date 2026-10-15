# One-time foundation provisioning

The manual `provision-foundation` workflow (`infrastructure.yml`) provisions
one selected Terraform root: **bootstrap** for application state storage, or
**registry** for runtime images. It is disabled unless
`AZURE_FOUNDATION_ENABLED=true`. These automation paths have local tests but
have not been executed against Azure. Adding the workflow does not create
resources or change the running assessment deployment.

## Sequence

1. An administrator establishes GitHub OIDC trust, protected environments,
   least-privileged identities, a private runner and durable management state.
2. Run foundation provisioning with `component=bootstrap`; review the plan,
   approve, then confirm its second plan is clean.
3. Run it with `component=registry` and perform the same review.
4. Configure the application backend using the resulting state storage. Grant
   the separate application delivery identity only its necessary access.
5. Run image release, then application deployment as described in
   [delivery](delivery.md). These require separate manual dispatches and gates.

Bootstrap needs an **already existing management backend** outside the resource
groups it creates. Terraform initializes its backend before creating resources,
so the application state account cannot bootstrap itself. Use an independently
managed Azure Blob account/container with encryption, versioning, Entra access
and lease locking. Keep it outside assessment teardown. Backend state is
persistent; GitHub artifacts are not a substitute for state storage.

For an existing deployment, first migrate or import its operator-managed
bootstrap and registry state into the selected management keys through a
reviewed local Terraform procedure. Preserve private backups and verify resource
addresses and clean plans. Do not point automation at empty state for existing
resources. The workflow does not automatically migrate or discard state.

## Configure once

Use the identity and private-runner setup in [delivery](delivery.md), with two
additional protected GitHub environments:

- `azure-foundation-plan`: the foundation OIDC identity and planning secrets.
- `azure-foundation-apply`: the same identity/secrets, required review after
  the plan summary exists, and main-only branch protection.

Configure exact Azure federated subjects
`repo:OWNER/REPO:environment:azure-foundation-plan` and
`repo:OWNER/REPO:environment:azure-foundation-apply`. This foundation identity
is separate from application runtime and normal application delivery. Creating
resource groups requires subscription-level resource-group creation permission;
bootstrap also creates a scoped Blob role assignment. Use a reviewed custom
role/policy where practical and never give automation subscription Owner by
default. Register required Storage, ContainerRegistry and Authorization resource
providers administratively before running. No Entra directory write permission
is needed by this workflow; directory app setup stays an administrative task.

Use the existing ephemeral `odyn-azure` runner with access to management storage.
The bootstrap account's IP allowlist must suit the operators who will use it;
this workflow never opens its firewall to all Internet addresses.

Repository variables:

- `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`.
- `BOOTSTRAP_RESOURCE_GROUP`, `REGISTRY_RESOURCE_GROUP`: exact dedicated names.
- `AZURE_FOUNDATION_ENABLED`: leave unset/false until the setup is reviewed.

Set `AZURE_CLIENT_ID` in both foundation environments to the same foundation
identity. Set the following secrets identically in both environments:

| Secret | Contents |
|---|---|
| `BOOTSTRAP_TFVARS_JSON` | Complete variables from `infra/bootstrap/main.tf`: subscription, tenant, dedicated resource group/storage names, location, Blob operator object ID, explicit approved IPv4 ranges. |
| `REGISTRY_TFVARS_JSON` | Complete variables from `infra/artifacts/main.tf`: subscription, tenant, environment suffix, location and globally unique registry name. |
| `BOOTSTRAP_BACKEND_JSON` | Management backend configuration; key must be `BOOTSTRAP_RESOURCE_GROUP/bootstrap.tfstate`, replacing the uppercase placeholder with the actual group name. |
| `REGISTRY_BACKEND_JSON` | Management backend configuration; key must be `REGISTRY_RESOURCE_GROUP/registry.tfstate`, replacing the placeholder. |
| `PLAN_ENCRYPTION_KEY` | Base64-encoded random 32-byte artifact encryption key, as in the delivery guide. |

Both backend JSON objects require `resource_group_name`, `storage_account_name`,
`container_name`, `key` and boolean `use_azuread_auth: true`. Static account keys,
SAS tokens and client passwords are rejected. Backend resource groups must
be outside their managed target group. Optional backend subscription/tenant
must match the configured Azure destination. OIDC authentication is supplied
through the workflow environment.

The registry root names its group `rg-odyn-gateway-artifacts-ENVIRONMENT`;
`REGISTRY_RESOURCE_GROUP` must agree with the configured environment suffix.
There are no secret value inputs for OpenRouter in this workflow.

## Execution and recovery

Wait for passing push CI for the exact main commit, open Actions →
**provision-foundation**, choose `bootstrap` or `registry`, and run it. Review
the address/action summary and saved-plan hash before approving apply. Inspect
complete sensitive plan values privately when needed; the public summary is
not a full semantic diff. Approval authorizes the resulting Azure costs.

Plans are AES-GCM encrypted, retained for one day, and expire after one hour.
Apply verifies source, component, identity, destination, private inputs and plan
hash, and applies that saved plan without replanning. Deletions and replacements
are rejected. Foundation operations serialize; active applies are not canceled.
A second plan must return no changes or the run fails. Normal application
operations should not run during foundation changes; backend leases guard each
state independently and do not coordinate different roots.

Failures stop the run without automatic rollback. Inspect the durable management
state and Azure resources, and generate a new reviewed plan for recovery.
Private ephemeral-runner cleanup is mandatory; emergency Terraform state from
backend write failures must be recovered privately before destroying the runner.
No state or plain plan is uploaded to GitHub. Keep the management backend for
future recovery even after assessment resource groups have been removed.

Terraform backend behavior: [AzureRM backend](https://developer.hashicorp.com/terraform/language/backend/azurerm),
[state locking](https://developer.hashicorp.com/terraform/language/state/locking).
