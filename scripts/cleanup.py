"""Protected pipeline cleanup: encrypted durable recovery before group deletion.

Prepare is read-only for the deployment and writes one encrypted recovery blob.
Execute verifies the same run, source, identity and resource inventory. No key
value is fetched; Terraform state is handled only as private encrypted bytes.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SPEC = importlib.util.spec_from_file_location(
    "odyn_teardown", Path(__file__).with_name("teardown.py")
)
teardown = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teardown)
RECOVERY_SPEC = importlib.util.spec_from_file_location(
    "odyn_state_recovery", Path(__file__).with_name("state-recovery.py")
)
recovery = importlib.util.module_from_spec(RECOVERY_SPEC)
RECOVERY_SPEC.loader.exec_module(recovery)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def driver():
    return teardown.Teardown(
        os.environ["AZURE_SUBSCRIPTION_ID"],
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_ENVIRONMENT_SUFFIX"],
        os.environ["BOOTSTRAP_RESOURCE_GROUP"],
    )


def binding(d):
    account = json.loads(teardown.command(["az", "account", "show", "-o", "json"]))
    principal = account.get("user", {})
    if (
        account.get("id") != d.subscription
        or account.get("tenantId") != d.tenant
        or principal.get("type") != "servicePrincipal"
        or principal.get("name", "").lower() != os.environ["AZURE_CLIENT_ID"].lower()
    ):
        raise ValueError("Cleanup identity/destination mismatch")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Cleanup requires protected main")
    return {
        "source": os.environ["GITHUB_SHA"],
        "run": os.environ["GITHUB_RUN_ID"],
        "subscription": d.subscription,
        "tenant": d.tenant,
        "principal": principal["name"].lower(),
        "groups": d.groups,
    }


def inventory_identity(inventory):
    # Azure resource order and provisioning timestamps are not an approval boundary.
    return {
        group: sorted([r["id"].lower(), r["type"].lower()] for r in resources)
        for group, resources in inventory.items()
    }


def key():
    value = base64.b64decode(os.environ["PLAN_ENCRYPTION_KEY"], validate=True)
    if len(value) != 32:
        raise ValueError("Invalid recovery encryption key")
    return value


def backend(name, d):
    value = json.loads(os.environ[name])
    if value.get("use_azuread_auth") is not True or any(
        k in value for k in ("access_key", "sas_token", "client_secret")
    ):
        raise ValueError("Recovery requires Entra authentication")
    if (
        value.get("subscription_id", d.subscription) != d.subscription
        or value.get("tenant_id", d.tenant) != d.tenant
    ):
        raise ValueError("Recovery backend destination mismatch")
    if not all(
        isinstance(value.get(k), str) and value[k]
        for k in (
            "resource_group_name",
            "storage_account_name",
            "container_name",
            "key",
        )
    ):
        raise ValueError("Incomplete recovery backend")
    return value


def prepare(directory):
    d = driver()
    print("Cleanup stage: verify identity", flush=True)
    context = binding(d)
    print("Cleanup stage: inspect owned groups", flush=True)
    inventory = d.inspect()
    print("Cleanup stage: validate recovery backends", flush=True)
    management = backend("BOOTSTRAP_BACKEND_JSON", d)
    registry = backend("REGISTRY_BACKEND_JSON", d)
    app = backend("TF_BACKEND_JSON", d)
    if (
        management["resource_group_name"] in d.groups
        or registry["resource_group_name"] != management["resource_group_name"]
        or registry["storage_account_name"] != management["storage_account_name"]
        or registry["container_name"] != management["container_name"]
        or management["key"] != f"{d.groups[3]}/bootstrap.tfstate"
        or registry["key"] != f"{d.groups[2]}/registry.tfstate"
        or app["resource_group_name"] != d.groups[3]
    ):
        raise ValueError(
            "Recovery storage must survive cleanup; state destinations must agree"
        )
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    states = {}
    state_bytes = {}
    for name, config in (
        ("application", app),
        ("bootstrap", management),
        ("registry", registry),
    ):
        # Fail closed on an unavailable/missing backend, including partial setup.
        # An operator must explicitly recover that case rather than skip state.
        print(f"Cleanup stage: recover {name} state", flush=True)
        target = directory / f"{name}.tfstate"
        teardown.command(
            [
                "az",
                "storage",
                "blob",
                "download",
                "--account-name",
                config["storage_account_name"],
                "--container-name",
                config["container_name"],
                "--name",
                config["key"],
                "--file",
                str(target),
                "--auth-mode",
                "login",
                "--subscription",
                d.subscription,
                "--only-show-errors",
                "-o",
                "none",
            ]
        )
        raw_state = target.read_bytes()
        state = json.loads(raw_state)
        if not isinstance(state.get("resources"), list) or not state.get("lineage"):
            raise ValueError("Invalid recovered Terraform state")
        states[name] = state
        state_bytes[name] = base64.b64encode(raw_state).decode()
        target.unlink()
    # The long-lived restore archive is separate from the short-lived deletion
    # approval receipt, and uses its own protected recovery key.
    restorable = recovery.seal(
        {"schema": 1, "subscription": d.subscription, "states": state_bytes},
        d.subscription,
    )
    restore_path = directory / "state-recovery" / "recovery.enc"
    restore_path.parent.mkdir(mode=0o700)
    restore_path.write_bytes(restorable)
    restore_path.chmod(0o600)
    receipt = context | {
        "schema": 1,
        "created": int(time.time()),
        "inventory": inventory_identity(inventory),
    }
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(key()).encrypt(
        nonce,
        json.dumps({"receipt": receipt, "states": states}).encode(),
        b"odyn-cleanup-v1",
    )
    path = directory / "recovery.enc"
    path.write_bytes(encrypted)
    os.chmod(path, 0o600)
    blob = f"recovery/{context['run']}/{hashlib.sha256(encrypted).hexdigest()}.enc"
    print("Cleanup stage: upload encrypted durable recovery", flush=True)
    teardown.command(
        [
            "az",
            "storage",
            "blob",
            "upload",
            "--account-name",
            management["storage_account_name"],
            "--container-name",
            management["container_name"],
            "--name",
            blob,
            "--file",
            str(path),
            "--auth-mode",
            "login",
            "--overwrite",
            "false",
            "--subscription",
            d.subscription,
            "--only-show-errors",
            "-o",
            "none",
        ]
    )
    restore_blob = (
        f"recovery/state/{context['run']}/{hashlib.sha256(restorable).hexdigest()}.enc"
    )
    teardown.command(
        [
            "az",
            "storage",
            "blob",
            "upload",
            "--account-name",
            management["storage_account_name"],
            "--container-name",
            management["container_name"],
            "--name",
            restore_blob,
            "--file",
            str(restore_path),
            "--auth-mode",
            "login",
            "--overwrite",
            "false",
            "--subscription",
            d.subscription,
            "--only-show-errors",
            "-o",
            "none",
        ]
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(
            "### Cleanup review\nEncrypted recovery snapshot uploaded to retained management storage.\n"
        )
        stream.writelines(
            f"- `{group}`: {len(resources)} resources; entire group will be removed.\n"
            for group, resources in inventory.items()
        )
        stream.write(
            f"Recovery SHA-256: `{hashlib.sha256(encrypted).hexdigest()}`\nRecovery blob: `{blob}`\nManagement storage is retained and continues to incur small charges.\n"
        )
        stream.write(
            f"Restorable state archive SHA-256: `{hashlib.sha256(restorable).hexdigest()}`\nState archive blob: `{restore_blob}`\n"
        )


def execute(directory):
    d = driver()
    encrypted = (directory / "recovery.enc").read_bytes()
    bundle = json.loads(
        AESGCM(key()).decrypt(encrypted[:12], encrypted[12:], b"odyn-cleanup-v1")
    )
    receipt = bundle["receipt"]
    if receipt.get("schema") != 1 or not 0 <= time.time() - receipt["created"] <= 3600:
        raise ValueError("Cleanup approval expired")
    if any(receipt.get(k) != v for k, v in binding(d).items()):
        raise ValueError("Cleanup source/run/destination changed")
    expected = f"delete {d.subscription} {d.app}"
    if os.environ.get("CLEANUP_CONFIRMATION") != expected:
        raise ValueError("Cleanup confirmation mismatch")
    if inventory_identity(d.inspect()) != receipt["inventory"]:
        raise ValueError("Resource inventory changed; generate a new cleanup review")
    for group in d.groups:
        d.delete(group)
    if any(d.exists(group) for group in d.groups):
        raise RuntimeError("Cleanup absence check failed")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(
            "### Cleanup result\nAll four deployment groups verified absent. Management storage, Entra apps, runners outside these groups and GitHub Pages are retained; inspect these separately. Azure billing is delayed.\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        (prepare if args.action == "prepare" else execute)(args.directory)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        InvalidTag,
    ) as error:
        print(f"Cleanup failure category: {type(error).__name__}")
        # Provider messages/state can contain secrets; inspect privately, never log.
        print(
            "Cleanup stopped; deployment/state may be partial. Inspect privately before retrying."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
