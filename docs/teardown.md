# Remove the assessment deployment

## Protected pipeline option

For unattended execution use **Cleanup Azure deployment** (`cleanup.yml`). It
has safe validation-only defaults, durable encrypted state recovery, an exact
confirmation input, inventory/source/run/identity checks and a protected deletion
gate. See [pipeline cleanup setup](pipeline-lifecycle.md#4-protected-cleanup--only-when-the-demo-can-stop).
It deletes the same four groups as the operator script below. Management storage
and external runners remain outside its scope and may continue costing money.
The workflow passed [an isolated Azure cleanup test](https://github.com/Diwaker-Singh/Odyn-Gateway/actions/runs/39158982129)
with encrypted recovery, empty application/registry groups and a real state-storage
account. This is not proof that full APIM/Key Vault/Container Apps teardown has
been exercised. Live deployment deletion has not run.

## Interactive operator option

Run from the repository root after the demo is no longer needed. This command
is destructive: it removes the dedicated application, its managed Container
Apps group, the registry (including all images), and Terraform state storage.
It uses Azure resource-group deletion rather than Terraform destroy so private
Key Vault data-plane connectivity is not needed to delete the deployment.

Requirements: Azure CLI login to the intended subscription, deletion permission
on all four dedicated groups, Terraform with the existing application backend
initialized, and Python 3.13. Use the same operator configuration as deployment.
Provider registration and existing Entra applications are left in place; neither
is a paid runtime resource. GitHub Pages remains hosted and will no longer be
able to contact the deleted gateway. Other subscription resources are untouched.

Preview first (read-only Azure inventory):

```sh
./.venv/bin/python scripts/teardown.py \
  --subscription YOUR_SUBSCRIPTION_ID --tenant YOUR_TENANT_ID
```

The default environment is `demo10` and the state group is
`rg-odyn-gateway-tfstate`. Use `--environment` and `--state-group` for a different
dedicated deployment. Inspect the listed groups in Azure before proceeding;
all resources in those groups will be removed, including manual additions.

Execute with the same arguments and `--execute`. The script requires you to type
`delete YOUR_SUBSCRIPTION_ID rg-odyn-gateway-demo10` exactly. There is no
noninteractive confirmation bypass.

```sh
./.venv/bin/python scripts/teardown.py \
  --subscription YOUR_SUBSCRIPTION_ID --tenant YOUR_TENANT_ID --execute
```

Before deletion, it writes private inventory and recovered application/local
Terraform states under ignored `.secrets/teardown/`, with owner-only permissions.
If the application group exists, remote-state recovery must succeed. If it was
never created and the group is absent, missing application state does not block
cleanup. Keep these snapshots private: state can contain sensitive values.

Deletion order is application → managed group → registry → state storage.
Each group must be verified absent before proceeding. A lock, permission error,
or timeout stops the sequence and returns a failure; do not treat that as
successful cleanup. If the application is gone but its managed group remains,
the script stops when it cannot independently establish ownership. Investigate
that orphan in Azure before manually removing it. Fix blockers and rerun; already
absent groups are skipped.

The command deliberately does not remove deletion locks, purge soft-deleted
Key Vault secrets, change purge protection, or delete Entra applications. Azure
may retain soft-deleted services and show delayed billing. Successful absence
checks stop the active dedicated resources; they do not guarantee a zero bill,
refund accrued charges, or cover unrelated resources. Review Azure Cost
Management afterward.

Resource-group deletion leaves any retained Terraform state stale. Do not run
apply from that old state to recreate the deployment. Use a fresh deployment
backend/state when building it again.

Azure CLI group deletion semantics are documented in
[Microsoft's CLI reference](https://learn.microsoft.com/cli/azure/group/).
