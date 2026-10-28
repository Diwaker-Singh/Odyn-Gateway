"""Release provenance and encrypted saved-plan helpers for GitHub Actions.

The publication command uploads verified images; Terraform application remains
in protected workflow jobs. Configuration arrives through environment variables,
never shell interpolation.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent


def run(args):
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        directory = os.environ.get("ODYN_DELIVERY_DIR")
        if directory and Path(directory).is_dir():
            target = Path(directory) / "delivery-error.log"
            if not target.is_symlink():
                target.write_text(
                    (error.stderr or "")[-262144:] + (error.stdout or "")[-262144:]
                )
                target.chmod(0o600)
        raise
    return result.stdout.strip()


def diagnostics(directory):
    """Keep bounded failure logs encrypted; never upload raw plans or logs."""
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    logs = {}
    for name in (
        "init.log",
        "plan.log",
        "apply.log",
        "second-plan.log",
        "delivery-error.log",
    ):
        path = directory / name
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 262144))
                logs[name] = stream.read(262144).decode(errors="replace")
    payload = json.dumps(
        {
            "schema": 1,
            "source": os.environ.get("GITHUB_SHA"),
            "run": os.environ.get("GITHUB_RUN_ID"),
            "logs": logs,
        }
    ).encode()
    nonce = os.urandom(12)
    target = directory / "diagnostics.enc"
    target.write_bytes(
        nonce + AESGCM(key()).encrypt(nonce, payload, b"odyn-delivery-diagnostics-v1")
    )
    target.chmod(0o600)


def failure_category(directory):
    """Report a low-risk failure category without exposing Terraform output."""
    logs = {
        name: (directory / name).read_text(errors="replace").lower()
        for name in ("init.log", "plan.log", "delivery-error.log")
        if (directory / name).is_file() and not (directory / name).is_symlink()
    }
    content = "\n".join(logs.values())
    categories = (
        ("AZURE_AUTHORIZATION", ("authorizationfailed", "does not have authorization")),
        ("AZURE_QUOTA", ("quotaexceeded", "overquota", "quota exceeded")),
        ("AZURE_POLICY", ("requestdisallowedbypolicy", "disallowedbyazurepolicy")),
        (
            "AZURE_PROVIDER_REGISTRATION",
            ("is not registered", "missingregistrationforlocation"),
        ),
        (
            "AZURE_RESOURCE_GROUP",
            ("resourcegroupnotfound", "resource group was not found"),
        ),
        (
            "AZURE_RESOURCE_CONFLICT",
            ("resourcealreadyexists", "resource already exists"),
        ),
        ("TERRAFORM_STATE_LOCK", ("error acquiring the state lock", "state lock info")),
        (
            "NETWORK_OR_PRIVATE_ENDPOINT",
            ("no such host", "dial tcp", "connection timed out"),
        ),
    )
    for category, markers in categories:
        if any(marker in content for marker in markers):
            print(f"Safe failure category: {category}")
            return category
    category = next(
        (
            name
            for filename, name in (
                ("plan.log", "TERRAFORM_PLAN_FAILED"),
                ("init.log", "TERRAFORM_INIT_FAILED"),
                ("delivery-error.log", "DELIVERY_HELPER_FAILED"),
            )
            if logs.get(filename)
        ),
        "TERRAFORM_FAILURE_UNCLASSIFIED",
    )
    print(f"Safe failure category: {category}")
    return category


def classify_init_failure(content):
    """Map private Terraform init output to a non-sensitive diagnostic class."""
    text = content.lower()
    categories = (
        (
            "BACKEND_AUTHENTICATION",
            (
                "authenticationfailed",
                "unauthorized",
                "forbidden",
                "status code 403",
                " 403",
            ),
        ),
        (
            "NETWORK_OR_PRIVATE_ENDPOINT",
            (
                "no such host",
                "dial tcp",
                "connection timed out",
                "context deadline exceeded",
            ),
        ),
        (
            "BACKEND_STORAGE_ACCESS",
            (
                "failed to get existing workspaces",
                "listing blobs",
                "storageaccountnotfound",
                "containernotfound",
                "failed to retrieve keys",
            ),
        ),
        (
            "OIDC_CONFIGURATION",
            (
                "oidc",
                "requesting oidc token",
                "actions_id_token_request_url",
                "client secret or assertion",
            ),
        ),
        (
            "PROVIDER_REGISTRY_ACCESS",
            (
                "failed to query available provider packages",
                "could not retrieve the list of available versions",
                "registry.terraform.io",
            ),
        ),
        (
            "PROVIDER_CHECKSUM",
            (
                "doesn't match any of the checksums",
                "does not match any of the checksums",
                "checksum list",
            ),
        ),
        (
            "PROVIDER_INSTALLATION",
            (
                "failed to install provider",
                "error while installing",
                "error downloading",
            ),
        ),
        (
            "BACKEND_CONFIGURATION",
            (
                "invalid backend configuration",
                "failed to get backend",
                "backend configuration changed",
                "missing required argument",
            ),
        ),
        ("TERRAFORM_STATE_LOCK", ("error acquiring the state lock", "state lock info")),
    )
    for category, markers in categories:
        if any(marker in text for marker in markers):
            return category
    return "TERRAFORM_INIT_OTHER"


def init_failure_signals(content):
    """Return only fixed safe indicators that explain backend access failures."""
    text = content.lower()
    markers = (
        (
            "storage_account_missing",
            ("storageaccountnotfound", "storage account was not found"),
        ),
        ("state_container_missing", ("containernotfound", "container was not found")),
        (
            "storage_authorization_denied",
            (
                "authorizationpermissionmismatch",
                "authorizationfailure",
                "status code 403",
                " 403",
                "forbidden",
            ),
        ),
        (
            "network_acl_denied",
            ("ipforbidden", "networkaclsvalidationfailure", "network access is denied"),
        ),
        ("blob_listing_failed", ("listing blobs", "failed to list blobs")),
        ("workspace_discovery_failed", ("failed to get existing workspaces",)),
    )
    found = [name for name, values in markers if any(value in text for value in values)]
    return found or ["no_specific_safe_marker"]


def save_failure(error):
    """Append a bounded helper failure to the private log for encryption."""
    directory = os.environ.get("ODYN_DELIVERY_DIR")
    if not directory:
        return
    root = Path(directory)
    if not root.is_dir():
        return
    target = root / "delivery-error.log"
    if target.is_symlink():
        return
    with target.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{type(error).__name__}: {str(error)[-32768:]}\n")
    target.chmod(0o600)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def validate_release(record, source_sha, registry):
    if record.get("schema") != 1 or record.get("source_sha") != source_sha:
        raise ValueError("Release source/schema mismatch")
    if not re.fullmatch(r"[a-z0-9]+\.azurecr\.io", registry):
        raise ValueError("Invalid registry hostname")
    for name in ("gateway", "collector"):
        image = record["images"][name]
        repository = "odyn-gateway" if name == "gateway" else "odyn-collector"
        if not re.fullmatch(
            re.escape(f"{registry}/{repository}@sha256:") + r"[a-f0-9]{64}",
            image["reference"],
        ):
            raise ValueError("Invalid published image reference")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image["local_id"]):
            raise ValueError("Invalid local image ID")
    return record


def plan_summary(plan, resource_group):
    if plan.get("errored"):
        raise ValueError("Plan is not applyable")
    if any(c.get("status") != "pass" for c in plan.get("checks", [])):
        raise ValueError("Plan checks are failed or unknown")
    changes = plan.get("resource_changes", [])
    if plan.get("applyable") is False and any(
        c["change"]["actions"] != ["no-op"] for c in changes
    ):
        raise ValueError("Plan is not applyable")
    groups = [
        c["change"].get("after", {}).get("name")
        for c in changes
        if c.get("type") == "azurerm_resource_group" and c["change"].get("after")
    ]
    if groups != [resource_group]:
        raise ValueError("Plan destination group mismatch")
    summary = []
    for change in changes:
        actions = change["change"]["actions"]
        if "delete" in actions:
            raise ValueError(
                "Deletion/replacement requires the local operator workflow"
            )
        after = change["change"].get("after") or {}
        if after.get("resource_group_name", resource_group) != resource_group:
            raise ValueError("Plan touches another resource group")
        summary.append({"address": change["address"], "actions": actions})
    return summary


def key():
    value = base64.b64decode(os.environ["PLAN_ENCRYPTION_KEY"], validate=True)
    if len(value) != 32:
        raise ValueError("PLAN_ENCRYPTION_KEY must encode exactly 32 random bytes")
    return value


def context():
    """Bind plan/apply to source, destination, identity and private inputs."""
    account = json.loads(run(["az", "account", "show", "-o", "json"]))
    if account["id"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("Subscription mismatch")
    if account["tenantId"] != os.environ["AZURE_TENANT_ID"]:
        raise ValueError("Tenant mismatch")
    if account.get("user", {}).get("type") != "servicePrincipal":
        raise ValueError("Delivery requires a dedicated federated service principal")
    if account["user"]["name"].lower() != os.environ["AZURE_CLIENT_ID"].lower():
        raise ValueError("Federated principal mismatch")
    inputs = {
        "tfvars": json.loads(os.environ["APPLICATION_TFVARS_JSON"]),
        "backend": json.loads(os.environ["TF_BACKEND_JSON"]),
    }
    return {
        "source_sha": os.environ["GITHUB_SHA"],
        "component": os.environ.get("ODYN_INFRA_COMPONENT", "application"),
        "rollback_deployment_run_id": os.environ.get("ROLLBACK_DEPLOYMENT_RUN_ID", ""),
        "resource_group": os.environ["AZURE_RESOURCE_GROUP"],
        "registry": os.environ["ACR_LOGIN_SERVER"],
        "subscription": account["id"],
        "tenant": account["tenantId"],
        "principal": account["user"]["name"],
        "inputs_sha256": sha256(json.dumps(inputs, sort_keys=True).encode()),
    }


def infrastructure_root(directory):
    component = os.environ.get("ODYN_INFRA_COMPONENT", "application")
    if component not in ("application", "bootstrap", "registry"):
        raise ValueError("Unsupported infrastructure component")
    return (
        ROOT / "infra/envs/dev/azure"
        if component == "application"
        else directory / "root"
    )


def prepare_infrastructure(directory):
    """Copy a fixed Terraform root and attach a pre-existing management backend."""
    component = os.environ["ODYN_INFRA_COMPONENT"]
    if component not in ("bootstrap", "registry"):
        raise ValueError("Expected bootstrap or registry component")
    variables = json.loads(os.environ["APPLICATION_TFVARS_JSON"])
    backend = json.loads(os.environ["TF_BACKEND_JSON"])
    destination = os.environ["AZURE_RESOURCE_GROUP"]
    for field, expected in (
        ("subscription_id", os.environ["AZURE_SUBSCRIPTION_ID"]),
        ("tenant_id", os.environ["AZURE_TENANT_ID"]),
    ):
        if variables.get(field) != expected:
            raise ValueError("Infrastructure destination mismatch")
    expected_group = (
        variables.get("resource_group_name")
        if component == "bootstrap"
        else f"rg-odyn-gateway-artifacts-{variables['environment']}"
    )
    if expected_group != destination:
        raise ValueError("Infrastructure resource group mismatch")
    if backend.get("use_azuread_auth") is not True or any(
        field in backend for field in ("access_key", "sas_token", "client_secret")
    ):
        raise ValueError("Management backend must use Entra authentication")
    if backend.get("resource_group_name") == destination:
        raise ValueError("Management state must be outside the managed resource group")
    if (
        backend.get("subscription_id", os.environ["AZURE_SUBSCRIPTION_ID"])
        != os.environ["AZURE_SUBSCRIPTION_ID"]
    ):
        raise ValueError("Management backend subscription mismatch")
    if (
        backend.get("tenant_id", os.environ["AZURE_TENANT_ID"])
        != os.environ["AZURE_TENANT_ID"]
    ):
        raise ValueError("Management backend tenant mismatch")
    if backend.get("key") != f"{destination}/{component}.tfstate":
        raise ValueError("Use a destination-specific component state key")
    if not all(
        backend.get(k)
        for k in ("resource_group_name", "storage_account_name", "container_name")
    ):
        raise ValueError("Management backend storage/container missing")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    root = infrastructure_root(directory)
    root.mkdir(mode=0o700, exist_ok=True)
    source = ROOT / "infra" / ("bootstrap" if component == "bootstrap" else "artifacts")
    for path in [*source.glob("*.tf"), source / ".terraform.lock.hcl"]:
        shutil.copyfile(path, root / path.name)
    (root / "backend.tf").write_text('terraform {\n  backend "azurerm" {}\n}\n')
    (directory / "application.tfvars.json").write_text(json.dumps(variables))
    (directory / "backend.json").write_text(json.dumps(backend))


def prepare(directory, receipt):
    rollback_id = os.environ.get("ROLLBACK_DEPLOYMENT_RUN_ID", "")
    deployment = json.loads(receipt.read_text())
    if rollback_id:
        validate_deployment(deployment, workflow_run(rollback_id), rollback_id)
        record = deployment["release"]
    else:
        record = deployment
    record = validate_release(
        record,
        record["source_sha"] if rollback_id else os.environ["GITHUB_SHA"],
        os.environ["ACR_LOGIN_SERVER"],
    )
    variables = json.loads(os.environ["APPLICATION_TFVARS_JSON"])
    backend = json.loads(os.environ["TF_BACKEND_JSON"])
    if variables.get("subscription_id") != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("tfvars subscription mismatch")
    if variables.get("tenant_id") != os.environ["AZURE_TENANT_ID"]:
        raise ValueError("tfvars tenant mismatch")
    if not backend.get("use_azuread_auth") or any(
        name in backend for name in ("access_key", "sas_token", "client_secret")
    ):
        raise ValueError("Backend must use Entra auth without static credentials")
    variables.update(
        container_image=record["images"]["gateway"]["reference"],
        collector_image=record["images"]["collector"]["reference"],
    )
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    (directory / "application.tfvars.json").write_text(json.dumps(variables))
    (directory / "backend.json").write_text(json.dumps(backend))
    (directory / "release.json").write_text(json.dumps(record))


def workflow_run(run_id):
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise ValueError("Invalid workflow run ID")
    return json.loads(
        run(
            [
                "gh",
                "api",
                f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}",
            ]
        )
    )


def validate_deployment(record, metadata, run_id):
    """Only a completed deployment in this repository/profile can be restored."""
    current = context()
    if (
        str(metadata.get("id")) != str(run_id)
        or metadata.get("path") != ".github/workflows/deploy.yml"
        or metadata.get("event") not in ("workflow_dispatch", "workflow_call")
        or metadata.get("head_branch") != "main"
        or metadata.get("status") != "completed"
        or metadata.get("conclusion") != "success"
        or metadata.get("repository", {}).get("full_name")
        != os.environ["GITHUB_REPOSITORY"]
        or record.get("schema") != 1
        or record.get("deployment_run_id") != str(run_id)
        or record.get("deployment_source") != metadata.get("head_sha")
        or record.get("target") != os.environ.get("DELIVERY_TARGET", "deployment")
        or record.get("context", {}).get("inputs_sha256") != current["inputs_sha256"]
        or any(
            record.get("context", {}).get(k) != current[k]
            for k in ("subscription", "tenant", "resource_group", "registry")
        )
    ):
        raise ValueError("Rollback deployment provenance or configuration mismatch")
    validate_release(
        record["release"],
        record["release"]["source_sha"],
        os.environ["ACR_LOGIN_SERVER"],
    )


def deployment_record(directory):
    """Publish image provenance only after apply, no-change plan and smoke pass."""
    record = {
        "schema": 1,
        "deployment_run_id": os.environ["GITHUB_RUN_ID"],
        "deployment_source": os.environ["GITHUB_SHA"],
        "target": os.environ.get("DELIVERY_TARGET", "deployment"),
        "context": context(),
        "release": json.loads((directory / "release.json").read_text()),
    }
    (directory / "deployment.json").write_text(json.dumps(record))


def rollback_plan(rendered):
    """Rollback changes only existing container images; unrelated drift stops it."""
    changed = 0
    for resource in rendered.get("resource_changes", []):
        change = resource["change"]
        if change["actions"] == ["no-op"]:
            continue
        if resource["type"] != "azurerm_container_app" or change["actions"] != [
            "update"
        ]:
            raise ValueError("Rollback must update existing container images only")

        def differences(before, after, unknown, path=()):
            if unknown is True:
                if path not in (
                    ("latest_revision_name",),
                    ("latest_revision_fqdn",),
                    ("latest_ready_revision_name",),
                    ("outbound_ip_addresses",),
                ):
                    raise ValueError("Unexpected unknown value in rollback plan")
                return []
            if isinstance(before, dict) and isinstance(after, dict):
                return [
                    p
                    for k in before.keys() | after.keys()
                    for p in differences(
                        before.get(k),
                        after.get(k),
                        (unknown or {}).get(k, False),
                        (*path, k),
                    )
                ]
            if (
                isinstance(before, list)
                and isinstance(after, list)
                and len(before) == len(after)
            ):
                return [
                    p
                    for i, (a, b) in enumerate(zip(before, after))
                    for p in differences(
                        a,
                        b,
                        unknown[i]
                        if isinstance(unknown, list) and i < len(unknown)
                        else False,
                        (*path, i),
                    )
                ]
            return [] if before == after else [path]

        paths = differences(
            change.get("before"), change.get("after"), change.get("after_unknown", {})
        )
        if any(
            len(p) != 5 or p[:3] != ("template", 0, "container") or p[-1] != "image"
            for p in paths
        ):
            raise ValueError(
                "Rollback would change configuration beyond container images"
            )
        changed += len(paths)
    return changed


def adopt_bootstrap_group(directory):
    """Import only an empty, explicitly project-owned destination group.

    Precreation permits narrow group-scoped delivery roles after full cleanup.
    Existing application resources require state recovery, never this shortcut.
    The bootstrap root's exact Terraform ownership tags are also accepted: that
    root defines the same project/purpose/owner tuple and may predate this
    execution pipeline. No other Terraform-tagged group is adopted.
    """
    current = context()
    root = infrastructure_root(directory)
    address = {
        "bootstrap": "azurerm_resource_group.state",
        "registry": "azurerm_resource_group.artifacts",
        "application": "module.llm_gateway.module.azure[0].azurerm_resource_group.main",
    }[os.environ.get("ODYN_INFRA_COMPONENT", "application")]
    base = ["terraform", f"-chdir={root}"]
    backend = json.loads(os.environ["TF_BACKEND_JSON"])
    # A genuinely new state and a backend failure both make Terraform's state
    # command exit nonzero. Establish absence through authenticated Blob metadata
    # instead of treating an arbitrary Terraform error as an empty backend.
    state_exists = json.loads(
        run(
            [
                "az",
                "storage",
                "blob",
                "exists",
                "--auth-mode",
                "login",
                "--account-name",
                backend["storage_account_name"],
                "--container-name",
                backend["container_name"],
                "--name",
                backend["key"],
                "-o",
                "json",
            ]
        )
    )["exists"]
    if not isinstance(state_exists, bool):
        raise TypeError("Backend existence was not established")
    state_addresses = (
        set(run([*base, "state", "list"]).splitlines()) if state_exists else set()
    )
    deployment_address = "azurerm_resource_group_template_deployment.container"
    bootstrap = os.environ.get("ODYN_INFRA_COMPONENT", "application") == "bootstrap"
    if address in state_addresses and (
        not bootstrap or deployment_address in state_addresses
    ):
        return
    group = os.environ["AZURE_RESOURCE_GROUP"]
    if json.loads(run(["az", "group", "exists", "-n", group, "-o", "json"])) is False:
        return  # Normal Terraform creation without precreation is still supported.
    metadata = json.loads(run(["az", "group", "show", "-n", group, "-o", "json"]))
    tags = metadata.get("tags", {})
    execution_owned = tags.get("managed_by") == "odyn-execution-pipeline"
    registry_owned = False
    if os.environ.get("ODYN_INFRA_COMPONENT") == "registry":
        variables = json.loads((directory / "application.tfvars.json").read_text())
        environment = variables.get("environment")
        registry_group = f"rg-odyn-gateway-artifacts-{environment}"
        registry_owned = (
            isinstance(environment, str)
            and group == registry_group
            and tags
            == {
                "project": "odyn-gateway",
                "environment": environment,
                "managed_by": "terraform",
            }
        )
    bootstrap_owned = os.environ.get(
        "ODYN_INFRA_COMPONENT"
    ) == "bootstrap" and tags == {
        "project": "odyn-gateway",
        "purpose": "terraform-state",
        "managed_by": "terraform",
    }
    application_owned = False
    if os.environ.get("ODYN_INFRA_COMPONENT") == "application":
        variables = json.loads((directory / "application.tfvars.json").read_text())
        environment = variables.get("environment")
        application_owned = (
            isinstance(environment, str)
            and variables.get("resource_group_name") == group
            and tags
            == {
                "project": "odyn-gateway",
                "environment": environment,
                "managed_by": "terraform",
            }
        )
    if (
        not execution_owned
        and not bootstrap_owned
        and not registry_owned
        and not application_owned
    ):
        raise ValueError("Unmanaged existing group requires explicit state recovery")
    if address not in state_addresses:
        if application_owned and state_addresses:
            raise ValueError(
                "Existing application state requires explicit state recovery"
            )
        if json.loads(run(["az", "resource", "list", "-g", group, "-o", "json"])):
            raise ValueError(
                "Cannot import a nonempty group through bootstrap adoption"
            )
        run(
            [
                *base,
                "import",
                "-input=false",
                "-lock-timeout=60s",
                f"-var-file={directory / 'application.tfvars.json'}",
                address,
                metadata["id"],
            ]
        )

    # Azure retains resource-group deployment history when the target resources
    # have been removed. Adopt only the bootstrap's fixed deployment name in its
    # explicitly owned group; the subsequent Terraform plan remains the source
    # of truth and will expose any configuration drift before apply.
    if bootstrap and deployment_address not in state_addresses:
        deployment_name = run(
            [
                "az",
                "deployment",
                "group",
                "list",
                "--resource-group",
                group,
                "--query",
                "[?name=='state-container'].name | [0]",
                "-o",
                "tsv",
            ]
        )
        if deployment_name:
            if deployment_name != "state-container":
                raise ValueError("Unexpected bootstrap deployment was returned")
            provisioning_state = run(
                [
                    "az",
                    "deployment",
                    "group",
                    "show",
                    "--resource-group",
                    group,
                    "--name",
                    "state-container",
                    "--query",
                    "properties.provisioningState",
                    "-o",
                    "tsv",
                ]
            )
            if provisioning_state != "Succeeded":
                raise ValueError("Existing bootstrap deployment is not successful")
            deployment_id = (
                f"/subscriptions/{current['subscription']}/resourceGroups/{group}"
                "/providers/Microsoft.Resources/deployments/state-container"
            )
            run(
                [
                    *base,
                    "import",
                    "-input=false",
                    "-lock-timeout=60s",
                    f"-var-file={directory / 'application.tfvars.json'}",
                    deployment_address,
                    deployment_id,
                ]
            )


def seal(directory):
    plan = directory / "application.tfplan"
    rendered = json.loads(
        run(
            [
                "terraform",
                f"-chdir={infrastructure_root(directory)}",
                "show",
                "-json",
                str(plan),
            ]
        )
    )
    if os.environ.get("ROLLBACK_DEPLOYMENT_RUN_ID"):
        rollback_plan(rendered)
    receipt = context() | {
        "schema": 1,
        "created": int(time.time()),
        "plan_sha256": sha256(plan.read_bytes()),
        "changes": plan_summary(rendered, os.environ["AZURE_RESOURCE_GROUP"]),
    }
    bundle = json.dumps(
        {
            "receipt": receipt,
            "plan": base64.b64encode(plan.read_bytes()).decode(),
            "release": json.loads((directory / "release.json").read_text())
            if (directory / "release.json").exists()
            else None,
        }
    ).encode()
    nonce = os.urandom(12)
    (directory / "plan.enc").write_bytes(
        nonce + AESGCM(key()).encrypt(nonce, bundle, b"odyn-plan-v1")
    )
    # Only addresses/actions and plan hash are published in the review summary.
    summary = [
        "### Exact deployment plan",
        f"SHA-256: `{receipt['plan_sha256']}`",
        f"Source: `{receipt['source_sha']}`",
        "",
        "| Resource | Actions |",
        "|---|---|",
    ]
    summary += [
        f"| `{c['address']}` | {', '.join(c['actions'])} |" for c in receipt["changes"]
    ]
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write("\n".join(summary) + "\n")


def unseal(directory):
    os.chmod(directory, 0o700)
    ciphertext = (directory / "plan.enc").read_bytes()
    bundle = json.loads(
        AESGCM(key()).decrypt(ciphertext[:12], ciphertext[12:], b"odyn-plan-v1")
    )
    receipt = bundle["receipt"]
    age = time.time() - receipt["created"]
    if receipt.get("schema") != 1 or not 0 <= age <= 3600:
        raise ValueError("Plan is expired or has an invalid schema")
    if any(receipt.get(k) != v for k, v in context().items()):
        raise ValueError("Plan source, destination, principal or inputs changed")
    plan = base64.b64decode(bundle["plan"], validate=True)
    if sha256(plan) != receipt["plan_sha256"]:
        raise ValueError("Plan hash mismatch")
    (directory / "application.tfplan").write_bytes(plan)
    if bundle.get("release"):
        validate_release(
            bundle["release"],
            bundle["release"]["source_sha"],
            os.environ["ACR_LOGIN_SERVER"],
        )
        (directory / "release.json").write_text(json.dumps(bundle["release"]))


def publication(directory):
    inspected = json.loads((directory / "images.json").read_text())
    scanned = {tag: image for image in inspected for tag in image["RepoTags"]}
    record = {"schema": 1, "source_sha": os.environ["GITHUB_SHA"], "images": {}}
    for name, repository in (
        ("gateway", "odyn-gateway"),
        ("collector", "odyn-collector"),
    ):
        local_id = run(
            [
                "docker",
                "image",
                "inspect",
                f"{repository}:release",
                "--format",
                "{{.Id}}",
            ]
        )
        metadata = scanned[f"{repository}:release"]
        if (
            metadata["Id"] != local_id
            or metadata["Os"] != "linux"
            or metadata["Architecture"] != "amd64"
            or metadata["Config"]["User"] != "10001:10001"
        ):
            raise ValueError("Loaded image differs from scanned non-root amd64 image")
        target = f"{os.environ['ACR_LOGIN_SERVER']}/{repository}:sha-{os.environ['GITHUB_SHA']}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
        # Check absence first; tags can still race, so deployment always pins digests.
        repositories = json.loads(
            run(
                [
                    "az",
                    "acr",
                    "repository",
                    "list",
                    "--name",
                    os.environ["ACR_NAME"],
                    "-o",
                    "json",
                ]
            )
        )
        existing = []
        if repository in repositories:
            existing = json.loads(
                run(
                    [
                        "az",
                        "acr",
                        "repository",
                        "show-tags",
                        "--name",
                        os.environ["ACR_NAME"],
                        "--repository",
                        repository,
                        "-o",
                        "json",
                    ]
                )
            )
        if target.rsplit(":", 1)[1] in existing:
            raise ValueError("Publication tag already exists")
        run(["docker", "tag", f"{repository}:release", target])
        run(["docker", "push", target])
        digest = run(
            [
                "az",
                "acr",
                "repository",
                "show",
                "--name",
                os.environ["ACR_NAME"],
                "--image",
                target.split("/", 1)[1],
                "--query",
                "digest",
                "-o",
                "tsv",
            ]
        )
        reference = target.rsplit(":", 1)[0] + "@" + digest
        run(["docker", "pull", reference])
        pulled_id = run(
            ["docker", "image", "inspect", reference, "--format", "{{.Id}}"]
        )
        if pulled_id != local_id:
            raise ValueError("Published manifest does not map to the scanned image")
        record["images"][name] = {"reference": reference, "local_id": local_id}
    validate_release(record, os.environ["GITHUB_SHA"], os.environ["ACR_LOGIN_SERVER"])
    (directory / "release.json").write_text(json.dumps(record, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "prepare-infrastructure",
            "adopt-bootstrap-group",
            "seal",
            "unseal",
            "publish",
            "record-deployment",
            "diagnostics",
            "failure-category",
        ),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "diagnostics":
        diagnostics(args.directory)
    elif args.action == "failure-category":
        failure_category(args.directory)
    elif args.action == "prepare-infrastructure":
        prepare_infrastructure(args.directory)
    elif args.action == "prepare":
        prepare(args.directory, args.release)
    elif args.action == "adopt-bootstrap-group":
        adopt_bootstrap_group(args.directory)
    elif args.action == "record-deployment":
        deployment_record(args.directory)
    elif args.action == "seal":
        seal(args.directory)
    elif args.action == "unseal":
        unseal(args.directory)
    else:
        publication(args.directory)


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        InvalidTag,
        subprocess.SubprocessError,
    ) as error:
        save_failure(error)
        raise SystemExit("Delivery failed; sensitive diagnostics suppressed.") from None
