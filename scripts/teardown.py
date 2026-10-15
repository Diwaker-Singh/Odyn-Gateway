"""Explicit resource-group teardown for a dedicated Odyn assessment deployment.

Default: read-only inventory. Destruction requires --execute and a typed
confirmation. Azure group deletion intentionally leaves Terraform state stale;
private snapshots are retained for audit/recovery, not subsequent apply.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command(args):
    result = subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=120, cwd=ROOT
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed: {args[0]} {args[1]}; diagnostics suppressed"
        )
    return result.stdout


class Teardown:
    def __init__(self, subscription, tenant, environment, state_group, run=command):
        if not re.fullmatch(r"[a-z0-9]{2,8}", environment):
            raise ValueError("Invalid environment suffix")
        if not re.fullmatch(r"rg-odyn-gateway-[a-z0-9-]+", state_group):
            raise ValueError("State group must be a dedicated Odyn group")
        self.subscription, self.tenant, self.run = subscription, tenant, run
        self.app = f"rg-odyn-gateway-{environment}"
        self.managed = f"{self.app}-managed"
        self.groups = [
            self.app,
            self.managed,
            f"rg-odyn-gateway-artifacts-{environment}",
            state_group,
        ]
        if len(set(self.groups)) != 4:
            raise ValueError("Resource groups must be distinct")

    def az(self, *args):
        return self.run(
            [
                "az",
                *args,
                "--subscription",
                self.subscription,
                "--only-show-errors",
                "-o",
                "json",
            ]
        )

    def exists(self, group):
        return json.loads(self.az("group", "exists", "--name", group)) is True

    def inspect(self):
        account = json.loads(self.run(["az", "account", "show", "-o", "json"]))
        if (
            account.get("id") != self.subscription
            or account.get("tenantId") != self.tenant
        ):
            raise ValueError("Active Azure subscription/tenant does not match")
        inventory = {}
        for group in self.groups:
            if not self.exists(group):
                inventory[group] = []
                continue
            metadata = json.loads(self.az("group", "show", "--name", group))
            if (
                group != self.managed
                and metadata.get("tags", {}).get("managed_by") != "terraform"
            ):
                raise ValueError(f"Unrecognized ownership tags: {group}")
            inventory[group] = json.loads(
                self.az("resource", "list", "--resource-group", group)
            )
        # Never delete an arbitrary managed group merely because its name matches.
        if self.exists(self.managed):
            environments = [
                r
                for r in inventory[self.app]
                if r.get("type", "").lower() == "microsoft.app/managedenvironments"
            ]
            linked = False
            for env in environments:
                data = json.loads(
                    self.az(
                        "resource",
                        "show",
                        "--ids",
                        env["id"],
                        "--api-version",
                        "2024-03-01",
                    )
                )
                linked |= (
                    data.get("properties", {}).get("infrastructureResourceGroup")
                    == self.managed
                )
            if not linked:
                raise ValueError("Managed group ownership cannot be established; stop")
        return inventory

    def delete(self, group, timeout=3600):
        if not self.exists(group):
            return
        self.az("group", "delete", "--name", group, "--yes", "--no-wait")
        deadline = time.monotonic() + timeout
        while self.exists(group):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Deletion incomplete: {group}; later groups retained"
                )
            time.sleep(10)
        print(f"Verified absent: {group}")

    def execute(self, backup):
        inventory = self.inspect()  # Recheck destination/ownership at execution.
        backup.mkdir(parents=True, mode=0o700, exist_ok=False)
        (backup / "inventory.json").write_text(json.dumps(inventory, indent=2))
        # Remote state must be recoverable before its storage is removed.
        if self.exists(self.app):
            state = self.run(
                ["terraform", "-chdir=infra/envs/dev/azure", "state", "pull"]
            )
            parsed = json.loads(state)
            if not parsed.get("resources"):
                raise ValueError("Application exists but recovered state is empty")
            (backup / "application.tfstate").write_text(state)
        for base in (ROOT / ".secrets", ROOT / "infra"):
            for source in base.rglob("*.tfstate*"):
                if backup in source.parents or not source.is_file():
                    continue
                target = backup / "local" / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                shutil.copyfile(source, target)
        for path in backup.rglob("*"):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        print(f"Private recovery snapshot: {backup}")
        for group in self.groups:
            self.delete(group)
        if any(self.exists(group) for group in self.groups):
            raise RuntimeError("Final absence check failed")
        print("All four dedicated resource groups verified absent.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--environment", default="demo10")
    parser.add_argument("--state-group", default="rg-odyn-gateway-tfstate")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        teardown = Teardown(
            args.subscription, args.tenant, args.environment, args.state_group
        )
        inventory = teardown.inspect()
        for group, resources in inventory.items():
            print(f"{group}: {len(resources)} resources")
        if not args.execute:
            print(
                "Preview only. No deletion requested. Review all resources with Azure CLI."
            )
            return 0
        expected = f"delete {args.subscription} {teardown.app}"
        if input(f"Type exactly '{expected}' to delete these groups: ") != expected:
            raise ValueError("Confirmation did not match")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        teardown.execute(ROOT / ".secrets" / "teardown" / stamp)
        return 0
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, OSError):
        print(
            "Teardown stopped. Review resource state before retrying; private diagnostics suppressed."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
