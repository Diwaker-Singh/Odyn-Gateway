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
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout.strip()


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
    record = validate_release(
        json.loads(receipt.read_text()),
        os.environ["GITHUB_SHA"],
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


def adopt_bootstrap_group(directory):
    """Import only an empty, explicitly bootstrap-owned destination group.

    Precreation permits narrow group-scoped delivery roles after full cleanup.
    Existing application resources require state recovery, never this shortcut.
    """
    context()
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
    if state_exists and address in run([*base, "state", "list"]).splitlines():
        return
    group = os.environ["AZURE_RESOURCE_GROUP"]
    if json.loads(run(["az", "group", "exists", "-n", group, "-o", "json"])) is False:
        return  # Normal Terraform creation without precreation is still supported.
    metadata = json.loads(run(["az", "group", "show", "-n", group, "-o", "json"]))
    if metadata.get("tags", {}).get("managed_by") != "odyn-execution-pipeline":
        raise ValueError("Unmanaged existing group requires explicit state recovery")
    if json.loads(run(["az", "resource", "list", "-g", group, "-o", "json"])):
        raise ValueError("Cannot import a nonempty group through bootstrap adoption")
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
    receipt = context() | {
        "schema": 1,
        "created": int(time.time()),
        "plan_sha256": sha256(plan.read_bytes()),
        "changes": plan_summary(rendered, os.environ["AZURE_RESOURCE_GROUP"]),
    }
    bundle = json.dumps(
        {"receipt": receipt, "plan": base64.b64encode(plan.read_bytes()).decode()}
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
        ),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "prepare-infrastructure":
        prepare_infrastructure(args.directory)
    elif args.action == "prepare":
        prepare(args.directory, args.release)
    elif args.action == "adopt-bootstrap-group":
        adopt_bootstrap_group(args.directory)
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
    ):
        raise SystemExit("Delivery failed; sensitive diagnostics suppressed.") from None
