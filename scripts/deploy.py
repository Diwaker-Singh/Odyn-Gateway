"""Approval-gated saved-plan driver; no default cloud actions or destroy path."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT = ROOT / "infra/envs/dev/azure"
PLAN = ROOT / ".secrets/deploy/azure.tfplan"
RECEIPT = PLAN.with_suffix(".receipt.json")
TFVARS = ROOT / ".secrets/deploy/application.tfvars.json"


def run(args, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture, cwd=ROOT)


def config_hash():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "infra").rglob("*")):
        if (
            path.is_file()
            and ".terraform" not in path.parts
            and (
                path.suffix in (".tf", ".tfvars", ".tftpl", ".json", ".yaml", ".yml")
                or path.name == ".terraform.lock.hcl"
            )
        ):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def principal_binding(subscription, tenant):
    """Return a private, stable binding for the active Owner principal."""
    account = json.loads(
        run(["az", "account", "show", "--only-show-errors", "-o", "json"], True).stdout
    )
    if account.get("id") != subscription or account.get("tenantId") != tenant:
        raise ValueError("Azure destination mismatch")
    user = account.get("user") or {}
    principal_type = user.get("type")
    if principal_type == "user":
        principal_id = run(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
            True,
        ).stdout.strip()
    elif principal_type in ("servicePrincipal", "managedIdentity"):
        principal_id = run(
            [
                "az",
                "ad",
                "sp",
                "show",
                "--id",
                user.get("name", ""),
                "--query",
                "id",
                "-o",
                "tsv",
            ],
            True,
        ).stdout.strip()
    else:
        raise ValueError("unsupported Azure principal type")
    if not principal_id:
        raise ValueError("Azure principal ID unavailable")

    assignments = json.loads(
        run(
            [
                "az",
                "role",
                "assignment",
                "list",
                "--scope",
                f"/subscriptions/{subscription}",
                "--include-inherited",
                "-o",
                "json",
            ],
            True,
        ).stdout
    )
    effective_roles = sorted(
        (
            {"role": item.get("roleDefinitionName"), "scope": item.get("scope")}
            for item in assignments
            if item.get("principalId", "").lower() == principal_id.lower()
        ),
        key=lambda item: (item.get("role") or "", item.get("scope") or ""),
    )
    owner_effective = any(item.get("role") == "Owner" for item in effective_roles)
    snapshot = {
        "principal_type": principal_type,
        "principal_id_sha256": hashlib.sha256(
            principal_id.lower().encode()
        ).hexdigest(),
        "effective_roles": effective_roles,
        "owner_effective": owner_effective,
    }
    snapshot["sha256"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        choices=("preflight", "plan", "bind-principal", "review", "apply"),
        default="preflight",
    )
    for name in ("subscription", "tenant", "resource-group"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    terraform = os.environ.get("ODYN_TERRAFORM", "terraform")
    command = [terraform, f"-chdir={ENVIRONMENT}"]
    if args.action == "preflight":
        run([terraform, "fmt", "-check", "-recursive", str(ROOT / "infra")])
        run(command + ["validate"])
        return 0
    if not all((args.subscription, args.tenant, args.resource_group)):
        parser.error(
            "explicit subscription, tenant and dedicated resource group required"
        )
    if args.action == "review":
        run(command + ["show", str(PLAN)])
        print("Plan SHA256:", hashlib.sha256(PLAN.read_bytes()).hexdigest())
        return 0
    approval = (
        "ODYN_DEPLOY_APPROVED" if args.action == "apply" else "ODYN_PLAN_APPROVED"
    )
    if os.environ.get(approval) != "1":
        parser.error(f"refusing {args.action} without {approval}=1")
    try:
        account = json.loads(
            run(
                ["az", "account", "show", "--only-show-errors", "-o", "json"], True
            ).stdout
        )
        if (
            account.get("id") != args.subscription
            or account.get("tenantId") != args.tenant
        ):
            parser.error("Azure destination mismatch")
    except (ValueError, subprocess.CalledProcessError):
        parser.error("Azure destination could not be verified")
    os.environ.update(ARM_SUBSCRIPTION_ID=args.subscription, ARM_TENANT_ID=args.tenant)
    os.umask(0o077)

    if args.action == "bind-principal":
        receipt = json.loads(RECEIPT.read_text())
        if any(
            receipt.get(key) != getattr(args, key)
            for key in ("subscription", "tenant", "resource_group")
        ):
            parser.error("saved plan destination mismatch")
        if (
            receipt.get("config") != config_hash()
            or receipt.get("sha256") != hashlib.sha256(PLAN.read_bytes()).hexdigest()
        ):
            parser.error("saved plan or configuration changed")
        try:
            binding = principal_binding(args.subscription, args.tenant)
        except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
            parser.error("active principal and effective roles could not be verified")
        if not binding["owner_effective"]:
            parser.error("active principal lacks the required subscription Owner role")
        if receipt.get("principal_binding") not in (None, binding):
            parser.error(
                "saved plan is already bound to a different principal or role set"
            )
        receipt["principal_binding"] = binding
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        print("Active principal and effective Owner role bound to saved-plan receipt.")
        return 0

    if args.action == "plan":
        if PLAN.exists():
            parser.error(
                "existing saved plan must be explicitly archived before replacing"
            )
        PLAN.parent.mkdir(parents=True, exist_ok=True)
        if not TFVARS.is_file():
            parser.error("private application tfvars file is required")
        run(
            command
            + ["plan", "-out=" + str(PLAN), "-input=false", "-var-file=" + str(TFVARS)]
        )
        plan = json.loads(run(command + ["show", "-json", str(PLAN)], True).stdout)
        groups = [
            c["change"]["after"]["name"]
            for c in plan.get("resource_changes", [])
            if c.get("type") == "azurerm_resource_group" and c["change"].get("after")
        ]
        if groups != [args.resource_group] or any(
            (c["change"].get("after") or {}).get(
                "resource_group_name", args.resource_group
            )
            != args.resource_group
            for c in plan.get("resource_changes", [])
        ):
            PLAN.unlink()
            parser.error("saved plan resource groups do not match approval")
        try:
            binding = principal_binding(args.subscription, args.tenant)
        except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
            PLAN.unlink()
            parser.error("active principal and effective roles could not be verified")
        if not binding["owner_effective"]:
            PLAN.unlink()
            parser.error("active principal lacks the required subscription Owner role")
        RECEIPT.write_text(
            json.dumps(
                {
                    "sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
                    "config": config_hash(),
                    "subscription": args.subscription,
                    "tenant": args.tenant,
                    "resource_group": args.resource_group,
                    "principal_binding": binding,
                }
            )
        )
        print("Saved private plan created. Review explicitly before apply.")
        return 0
    receipt = json.loads(RECEIPT.read_text())
    if any(
        receipt.get(key) != getattr(args, key)
        for key in ("subscription", "tenant", "resource_group")
    ):
        parser.error("saved plan destination mismatch")
    if (
        receipt["config"] != config_hash()
        or receipt["sha256"] != hashlib.sha256(PLAN.read_bytes()).hexdigest()
    ):
        parser.error("saved plan or configuration changed")
    if os.environ.get("ODYN_REVIEWED_PLAN_SHA256") != receipt["sha256"]:
        parser.error("explicit reviewed plan SHA256 required")
    try:
        binding = principal_binding(args.subscription, args.tenant)
    except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
        parser.error("applying principal and effective roles could not be verified")
    if not binding["owner_effective"]:
        parser.error("applying principal lacks the required subscription Owner role")
    if receipt.get("principal_binding") != binding:
        parser.error(
            "applying principal or effective roles differ from saved-plan receipt"
        )
    if (
        input("Type the exact dedicated resource group to apply: ").strip()
        != args.resource_group
    ):
        parser.error("confirmation mismatch")
    run(command + ["apply", "-input=false", str(PLAN)])
    print(
        "Applied reviewed plan. Cloud proof, drift and separately approved teardown remain."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, KeyError):
        raise SystemExit(
            "Deployment failed; no sensitive command output recorded by driver."
        ) from None
