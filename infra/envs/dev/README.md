# Azure development environment

This root composes the Azure implementation for a development deployment. It
expects the shared module inputs and creates Azure resources for the edge,
API, private runtime, identity, shared Table store, secret reference and
monitoring path.

Keep environment-specific identifiers and private backend configuration in
local ignored files. `terraform.tfvars.example` contains placeholders only.
Initialize with the committed provider lock file and validate without a cloud
backend before producing any remote plan:

```sh
terraform init -backend=false -lockfile=readonly
terraform validate
```

For a real deployment, initialize the encrypted remote backend, use a
least-privileged operator identity, inspect the destination and exact saved
plan, and confirm cost/rollback scope before applying. Runtime identity
permissions are separate from deployment permissions. See the module and
[bootstrap guide](../../bootstrap/README.md).
