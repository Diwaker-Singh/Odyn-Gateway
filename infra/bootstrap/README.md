# Remote Terraform state bootstrap

This root creates storage prerequisites for an encrypted remote Terraform
backend. Use a dedicated state resource group and storage account, Entra-based
data-plane access, blob versioning/retention, and network restrictions suited
to the deployment runner. Do not use account keys or SAS tokens for routine
state access.

Provide environment-specific inputs through an ignored local variable file.
Never commit account names tied to a private deployment, object IDs, IP allow
lists, state, plans or credentials. Validate this root with the pinned provider
and backend disabled before planning a bootstrap deployment.

After provisioning, verify Entra data access and confirm concurrent Terraform
operations contend on the state lease. Configure application roots with the
private backend settings and `use_azuread_auth = true`. Treat state as
sensitive: Terraform's `sensitive` marker redacts display but does not encrypt
values inside state.

The execution workflow adds and removes its temporary runner subnet rules on
both state accounts. This root ignores changes to that subnet list so applying
the foundation cannot disconnect its own runner. Terraform still controls the
default-deny firewall, operator IP rules and public-access policy. Runner
retirement must remove its subnet rules before deleting the execution network.

Foundation planning checks the actual `tfstate` container through ARM. If the
container is missing while old deployment history remains, it recreates only
that template deployment. The saved-plan gate requires authenticated absence
evidence and rejects replacement of any other resource.
