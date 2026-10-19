# State preservation and recovery

State recovery restores the exact Terraform state bytes. It does not itself
recreate the application, restore Table data or recover provider credentials.
The three snapshots cover application, state-storage bootstrap and registry.
Never commit their plaintext, ciphertext or encryption key to source control.

## Recovery controls

- Archives use AES-256-GCM, a versioned authenticated header and bounded gzip
  decompression. The caller pins the encrypted archive's SHA-256.
- Validation checks all three snapshots, Terraform state version/lineage/serial,
  subscription and gateway resource-group ownership before Azure operations.
- Archive verification runs on GitHub-hosted execution without Azure access.
  Only ciphertext is retained as an immutable Actions artifact for 90 days.
  Keep an independent private copy and its key beyond that retention window.
- Restore runs on private execution with separate foundation/application OIDC
  environments. Account IDs, tenant, subscription, principal and protected main
  must match the selected backend configuration.
- Every selected target must be absent before any upload. Uploads also use
  `If-None-Match: *`; a race or existing state fails rather than overwriting it.
  Restored bytes are downloaded and compared. Multiple blobs are not one atomic
  transaction: inspect a partial failure and restore only a missing component.
- Recovery creates no storage accounts and makes no provider calls. Provision
  the required empty, Entra-only, default-deny backends first.

## Protected configuration

| Environment | Identity/access | Secrets |
|---|---|---|
| `azure-recovery-archive` | No Azure identity; main-only owner approval | `RECOVERY_ENCRYPTION_KEY` |
| `azure-recovery-foundation` | Foundation OIDC; management-account metadata read and Blob Data Contributor | Recovery key and `STATE_BACKENDS_JSON` |
| `azure-recovery-application` | Deployment OIDC; application-state account metadata read and Blob Data Contributor | Recovery key and `STATE_BACKENDS_JSON` |

Each environment has `AZURE_SUBSCRIPTION_ID`; Azure restore environments also
have `AZURE_TENANT_ID` and `AZURE_CLIENT_ID`. `STATE_BACKENDS_JSON` maps
`bootstrap`, `registry`, and `application` to the corresponding standard
Entra-only backend JSON (resource group, storage account, container, key,
subscription and tenant). Store it in protected environment secrets. Do not
include account keys, SAS tokens or client secrets. Use a random 32-byte,
base64-encoded recovery key; keep an owner-only copy independent of the archive.

The normal state, registry, management and execution groups can remain empty
after resource retirement. These metadata/permission anchors keep scoped OIDC
access available without retaining VMs, disks, storage or networking. Deleting
the groups also deletes their scoped access and requires administrative trust
restoration. Role assignments do not replace the protected approval gates.

## Preserve a local snapshot

Normal `cleanup.yml` snapshots also produce the compatible
`encrypted-state-recovery` artifact and upload a copy to management storage.
Configure `RECOVERY_ENCRYPTION_KEY` on the selected cleanup-plan environment;
use a separate key for a disposable profile. This restorable archive is distinct
from the short-lived `PLAN_ENCRYPTION_KEY` deletion receipt. A successful
snapshot remains usable when a later deletion job fails, once the run has ended.

Export fresh snapshots privately as `application.tfstate`, `bootstrap.tfstate`
and `registry.tfstate` into an owner-only directory. Set the subscription and
recovery key through private environment configuration, then run:

```sh
python scripts/state-recovery.py create \
  --directory /private/state-snapshots --archive /private/recovery.enc
python scripts/state-recovery.py verify \
  --archive /private/recovery.enc --sha256 ENCRYPTED_ARCHIVE_SHA256
```

The `state-recovery.yml` archive action accepts base64 of that **already encrypted**
file and its SHA-256. Keep the key in the protected secret, never the workflow
input. The bounded operator-import path accepts at most 60,000 base64 characters,
below GitHub's dispatch payload limit; oversized archives are rejected. Larger
backups need an artifact-producing private backup job, not plaintext input or
unreviewed splitting. The ciphertext is read from the event file rather than
echoed as a step environment value.

## Restore through pipelines

Use a newly approved spending window before provisioning backends or execution.
Provisioning and `STATE_RECOVERY_ENABLED` are disabled after retirement.

1. Start independent execution. Bootstrap management storage with
   `management.yml`. Retained permission anchors alone do not hold state data.
2. Enable protected state recovery, then run `state-recovery.yml` with
   `action=restore-foundation`, the successful archive run ID, pinned ciphertext
   SHA-256 and confirmation `restore SUBSCRIPTION_ID foundation`.
3. Run the bootstrap foundation pipeline to recreate application-state storage.
4. Run `restore-application` with confirmation
   `restore SUBSCRIPTION_ID application`, then provision registry, publish
   verified images and plan/apply the application through their normal gates.
5. Inspect actual restore/apply jobs and the second plan; a successful
   validation-only or skipped job is not restoration evidence. Retire private
   execution when finished.

If a recovered backend already contains state, verify its lineage and use the
existing state rather than attempting to overwrite it with this workflow.

The lifecycle controller can orchestrate this ordering: select the deployment
profile and pass `recovery_run_id` plus `recovery_sha256`. It restores foundation
states after management bootstrap, and application state after application-state
storage exists. It rejects restoring deployment state into a disposable profile.
Without archive inputs, it follows the normal fresh-setup path.

Individual `restore-bootstrap` and `restore-registry` actions allow explicitly
recovering a missing blob after inspecting a partial foundation restore. Existing
blobs are never replaced automatically. Keep provisioning disabled while
resolving missing connectivity, mismatched identity or existing-state conflicts.

## Service recovery boundaries

Restored state describes resources that may have been deleted. Terraform must
refresh it and produce a new saved plan before apply; do not reuse an old plan.
Soft-deleted Key Vault/APIM names may require explicit recovery or a reviewed
new naming strategy. No purge is performed. Provider keys stay out of state:
recover/re-enter them separately and start in stub mode before approving live
provider calls. Refresh the hosted client's public endpoint after rebuilding.

Local tests cover cryptographic integrity, wrong keys/destinations, decompression
limits, protected identities, atomic create-only writes, readback and staged
orchestration. They do not prove a complete Azure restore. Check the current
[workflow status](workflow-coverage.md#execution-status) before making that claim.
