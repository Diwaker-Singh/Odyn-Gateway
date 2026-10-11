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
