"""Bootstrap or retire independent private Actions execution without Terraform state.

Administrative federation is established once outside this script. Registration
tokens enter Azure only as protected Run Command parameters, never VM custom data.
"""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "odyn-execution-pipeline"
ROLES = {
    "Contributor": "b24988ac-6180-42a0-ab88-20f7382dd24c",
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "AcrPush": "8311e382-0749-4cb8-b61a-304f252e45ec",
    "Role Based Access Control Administrator": "f58310d9-a9f6-439a-9e8d-f62e7b41a168",
}


def run(args, payload=None):
    result = subprocess.run(
        args, input=payload, capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode:
        raise RuntimeError("Execution operation failed; inspect Azure privately")
    return result.stdout.strip()


def az(*args):
    return json.loads(run(["az", *args, "--only-show-errors", "-o", "json"]) or "null")


def config():
    value = json.loads(os.environ["EXECUTION_CONFIG_JSON"])
    if not re.fullmatch(r"rg-odyn-gateway-execution-[a-z0-9]{2,12}", value["group"]):
        raise ValueError("Dedicated execution group required")
    if not re.fullmatch(r"[a-z0-9]+", value["location"]):
        raise ValueError("Invalid Azure location")
    network = ipaddress.ip_network(value["network"], strict=True)
    if network.version != 4 or not network.is_private or network.prefixlen != 24:
        raise ValueError("Use a dedicated private /24 execution network")
    if not isinstance(value["minutes"], int) or not 10 <= value["minutes"] <= 180:
        raise ValueError("Execution lifetime must be 10–180 whole minutes")
    if not isinstance(value.get("retain_group", False), bool):
        raise TypeError("retain_group must be a boolean")
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    uuid.UUID(subscription)
    uuid.UUID(os.environ["AZURE_TENANT_ID"])
    uuid.UUID(os.environ["AZURE_CLIENT_ID"])
    prefix = f"/subscriptions/{subscription}/resourceGroups/"
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", os.environ["GITHUB_REPOSITORY"]
    ):
        raise ValueError("Invalid GitHub repository")
    seen = set()
    for group in value.get("groups", []):
        if (
            not re.fullmatch(r"rg-odyn-gateway-[a-z0-9-]+", group["name"])
            or group["name"] == value["group"]
            or group["name"] in seen
        ):
            raise ValueError("Invalid or duplicate destination group")
        seen.add(group["name"])
        for grant in group["grants"]:
            uuid.UUID(grant["principal"])
            if grant["role"] not in ROLES:
                raise ValueError("Unapproved role; Owner is never assigned")
    for item in value.get("storage", []):
        if not item["id"].startswith(prefix) or not re.fullmatch(
            re.escape(prefix)
            + r"[^/]+/providers/Microsoft.Storage/storageAccounts/[a-z0-9]{3,24}",
            item["id"],
        ):
            raise ValueError("Storage must be in the configured subscription")
    vnet = value.get("application_vnet")
    if vnet and not re.fullmatch(
        re.escape(prefix)
        + r"[^/]+/providers/Microsoft.Network/virtualNetworks/[A-Za-z0-9_-]+",
        vnet,
    ):
        raise ValueError("Invalid application VNet")
    for zone in value.get("dns_zones", []):
        if not vnet or not re.fullmatch(
            re.escape(prefix)
            + r"[^/]+/providers/Microsoft.Network/privateDnsZones/privatelink\.[a-z0-9.]+",
            zone,
        ):
            raise ValueError("Explicit same-subscription private DNS zone required")
    return value


def identity():
    account = az("account", "show")
    if (
        account.get("id") != os.environ["AZURE_SUBSCRIPTION_ID"]
        or account.get("tenantId") != os.environ["AZURE_TENANT_ID"]
        or account.get("user", {}).get("type") != "servicePrincipal"
        or account["user"]["name"].lower() != os.environ["AZURE_CLIENT_ID"].lower()
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("Protected bootstrap identity/destination mismatch")


def owned(group):
    metadata = az("group", "show", "-n", group)
    if metadata.get("tags", {}).get("managed_by") != OWNER:
        raise ValueError("Refusing to adopt or delete an unowned execution group")


def restore_groups(value):
    """Precreate explicit groups and restore scoped rights, never subscription roles."""
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    for item in value.get("groups", []):
        name = item["name"]
        if az("group", "exists", "-n", name):
            tags = az("group", "show", "-n", name).get("tags", {})
            if tags.get("project") != "odyn-gateway" or tags.get("managed_by") not in (
                OWNER,
                "terraform",
            ):
                raise ValueError("Destination group is not owned by this solution")
        else:
            az(
                "group",
                "create",
                "-n",
                name,
                "-l",
                value["location"],
                "--tags",
                "project=odyn-gateway",
                f"managed_by={OWNER}",
            )
        scope = f"/subscriptions/{subscription}/resourceGroups/{name}"
        for grant in item["grants"]:
            role = ROLES[grant["role"]]
            existing = az("role", "assignment", "list", "--scope", scope)
            if any(
                assignment.get("principalId", "").lower() == grant["principal"].lower()
                and assignment.get("roleDefinitionId", "").lower().endswith("/" + role)
                and assignment.get("scope", "").lower() == scope.lower()
                for assignment in existing
            ):
                continue
            identifier = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}/{grant['principal']}/{role}")
            )
            az(
                "role",
                "assignment",
                "create",
                "--name",
                identifier,
                "--assignee-object-id",
                grant["principal"],
                "--assignee-principal-type",
                "ServicePrincipal",
                "--role",
                role,
                "--scope",
                scope,
            )


def network_links(value, remove=False):
    group = value["group"]
    vnet = f"/subscriptions/{os.environ['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{group}/providers/Microsoft.Network/virtualNetworks/execution"
    subnet = vnet + "/subnets/runner"
    link = "execution-" + group.rsplit("-", 1)[1]
    for item in value.get("storage", []):
        parts = item["id"].split("/")
        if remove:
            if not az("group", "exists", "-n", parts[4]):
                continue
            rules = az(
                "storage",
                "account",
                "network-rule",
                "list",
                "-g",
                parts[4],
                "--account-name",
                parts[-1],
            )
            if not any(
                rule.get("virtualNetworkResourceId", rule.get("id", "")).lower()
                == subnet.lower()
                for rule in rules.get("virtualNetworkRules", [])
            ):
                continue
        az(
            "storage",
            "account",
            "network-rule",
            "remove" if remove else "add",
            "-g",
            parts[4],
            "--account-name",
            parts[-1],
            "--subnet",
            subnet,
        )
    app = value.get("application_vnet")
    if app:
        parts = app.split("/")
        if not remove:
            az(
                "network",
                "vnet",
                "peering",
                "create",
                "-g",
                group,
                "--vnet-name",
                "execution",
                "-n",
                "to-application",
                "--remote-vnet",
                app,
                "--allow-vnet-access",
            )
            az(
                "network",
                "vnet",
                "peering",
                "create",
                "-g",
                parts[4],
                "--vnet-name",
                parts[-1],
                "-n",
                link,
                "--remote-vnet",
                vnet,
                "--allow-vnet-access",
            )
        else:
            peers = (
                az(
                    "network",
                    "vnet",
                    "peering",
                    "list",
                    "-g",
                    parts[4],
                    "--vnet-name",
                    parts[-1],
                )
                if az("group", "exists", "-n", parts[4])
                else []
            )
            matches = [peer for peer in peers if peer["name"] == link]
            if (
                matches
                and matches[0].get("remoteVirtualNetwork", {}).get("id", "").lower()
                != vnet.lower()
            ):
                raise ValueError("Peering ownership mismatch")
            if matches:
                az(
                    "network",
                    "vnet",
                    "peering",
                    "delete",
                    "-g",
                    parts[4],
                    "--vnet-name",
                    parts[-1],
                    "-n",
                    link,
                )
    for zone in value.get("dns_zones", []):
        parts = zone.split("/")
        if remove:
            if not az("group", "exists", "-n", parts[4]):
                continue
            links = az(
                "network",
                "private-dns",
                "link",
                "vnet",
                "list",
                "-g",
                parts[4],
                "-z",
                parts[-1],
            )
            matches = [item for item in links if item["name"] == link]
            if not matches:
                continue
            if (
                matches[0].get("virtualNetwork", {}).get("id", "").lower()
                != vnet.lower()
            ):
                raise ValueError("DNS link ownership mismatch")
        args = [
            "network",
            "private-dns",
            "link",
            "vnet",
            "delete" if remove else "create",
            "-g",
            parts[4],
            "-z",
            parts[-1],
            "-n",
            link,
        ]
        args += ["--yes"] if remove else ["-v", vnet, "--registration-enabled", "false"]
        az(*args)


def protected_start(value, token, deadline, stopping=False):
    """Do not place the registration token in cloud-init or command arguments."""
    source = (
        ROOT / "scripts" / ("execution-stop.sh" if stopping else "execution-start.sh")
    ).read_text()
    body = {
        "location": value["location"],
        "properties": {
            "source": {"script": source},
            "timeoutInSeconds": 720,
            "parameters": [
                {"name": "ODYN_REPOSITORY", "value": os.environ["GITHUB_REPOSITORY"]},
                {"name": "ODYN_DEADLINE", "value": str(deadline)},
                {
                    "name": "ODYN_RUNNER_NAME",
                    "value": "odyn-" + value["group"].rsplit("-", 1)[1],
                },
            ],
            "protectedParameters": [
                {
                    "name": "ODYN_REMOVAL_TOKEN"
                    if stopping
                    else "ODYN_REGISTRATION_TOKEN",
                    "value": token,
                }
            ],
        },
    }
    command_name = "retire" if stopping else "register"
    resource = f"/subscriptions/{os.environ['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{value['group']}/providers/Microsoft.Compute/virtualMachines/runner/runCommands/{command_name}?api-version=2023-03-01"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "command.json"
        path.write_text(json.dumps(body))
        path.chmod(0o600)
        az("rest", "--method", "put", "--url", resource, "--body", "@" + str(path))


def start(value):
    group = value["group"]
    if az("group", "exists", "-n", group):
        owned(group)
        if az("resource", "list", "-g", group):
            raise ValueError(
                "Execution already exists; retire it before starting a new lease"
            )
    restore_groups(value)
    if value.get("application_vnet"):
        app = az("network", "vnet", "show", "--ids", value["application_vnet"])
        if any(
            ipaddress.ip_network(value["network"]).overlaps(
                ipaddress.ip_network(prefix)
            )
            for prefix in app["addressSpace"]["addressPrefixes"]
        ):
            raise ValueError("Execution network overlaps the application network")
    az(
        "group",
        "create",
        "-n",
        group,
        "-l",
        value["location"],
        "--tags",
        "project=odyn-gateway",
        f"managed_by={OWNER}",
        f"execution_run={os.environ['GITHUB_RUN_ID']}",
    )
    deadline = int(time.time()) + value["minutes"] * 60
    az(
        "network",
        "vnet",
        "create",
        "-g",
        group,
        "-n",
        "execution",
        "--address-prefixes",
        value["network"],
        "--subnet-name",
        "runner",
        "--subnet-prefixes",
        value["network"],
    )
    az(
        "network",
        "vnet",
        "subnet",
        "update",
        "-g",
        group,
        "--vnet-name",
        "execution",
        "-n",
        "runner",
        "--service-endpoints",
        "Microsoft.Storage",
    )
    az("network", "nsg", "create", "-g", group, "-n", "runner")
    az(
        "network",
        "nsg",
        "rule",
        "create",
        "-g",
        group,
        "--nsg-name",
        "runner",
        "-n",
        "DenyInbound",
        "--priority",
        "100",
        "--access",
        "Deny",
        "--direction",
        "Inbound",
        "--protocol",
        "*",
        "--source-address-prefixes",
        "*",
        "--destination-address-prefixes",
        "*",
        "--source-port-ranges",
        "*",
        "--destination-port-ranges",
        "*",
    )
    az(
        "network",
        "public-ip",
        "create",
        "-g",
        group,
        "-n",
        "egress",
        "--sku",
        "Standard",
        "--allocation-method",
        "Static",
    )
    az(
        "network",
        "nic",
        "create",
        "-g",
        group,
        "-n",
        "runner",
        "--vnet-name",
        "execution",
        "--subnet",
        "runner",
        "--network-security-group",
        "runner",
        "--public-ip-address",
        "egress",
    )
    # Public IP provides explicit outbound connectivity; NSG denies ALL inbound.
    # The VM has no managed identity or Azure credential at rest.
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / "ssh"
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
        az(
            "vm",
            "create",
            "-g",
            group,
            "-n",
            "runner",
            "--image",
            "Ubuntu2404",
            "--size",
            "Standard_B2s",
            "--admin-username",
            "odynrunner",
            "--ssh-key-values",
            str(key) + ".pub",
            "--nics",
            "runner",
            "--storage-sku",
            "Standard_LRS",
            "--os-disk-delete-option",
            "Delete",
            "--custom-data",
            str(ROOT / "scripts/execution-install.sh"),
        )
    shutdown = time.strftime("%H%M", time.gmtime(deadline))
    az("vm", "auto-shutdown", "-g", group, "-n", "runner", "--time", shutdown)
    network_links(value)
    # Managed Run Command may start before cloud-init has installed the runner;
    # its script waits for the ready sentinel with a bounded timeout.
    token = os.environ.get("RUNNER_REGISTRATION_TOKEN")
    if not token:
        token = json.loads(
            run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runners/registration-token",
                ]
            )
        )["token"]
    protected_start(value, token, deadline)
    name = "odyn-" + group.rsplit("-", 1)[1]
    if not os.environ.get("GH_TOKEN"):
        print(
            json.dumps(
                {
                    "schema": 1,
                    "status": "registration_requested",
                    "group": group,
                    "deadline_epoch": deadline,
                    "runner": name,
                }
            )
        )
        return  # The self-hosted probe establishes readiness in token-only tests.
    for _ in range(30):
        runners = json.loads(
            run(
                [
                    "gh",
                    "api",
                    f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runners",
                ]
            )
        )["runners"]
        if any(r["name"] == name and r["status"] == "online" for r in runners):
            print(
                json.dumps(
                    {
                        "schema": 1,
                        "status": "ready",
                        "group": group,
                        "deadline_epoch": deadline,
                        "runner": name,
                    }
                )
            )
            return
        time.sleep(10)
    raise RuntimeError("Runner readiness timed out; run protected stop workflow")


def retire_resources(value):
    """Keep a free IAM scope while removing its billable execution resources.

    Complete-mode deletion is confined to an owned dedicated group. Unexpected
    resource types stop retirement rather than silently deleting manual additions.
    """
    group = value["group"]
    allowed = {
        "microsoft.network/virtualnetworks": {"execution"},
        "microsoft.network/networksecuritygroups": {"runner"},
        "microsoft.network/publicipaddresses": {"egress"},
        "microsoft.network/networkinterfaces": {"runner"},
        "microsoft.compute/virtualmachines": {"runner"},
        "microsoft.compute/virtualmachines/runcommands": {
            "runner/register",
            "runner/retire",
        },
        "microsoft.devtestlab/schedules": {"shutdown-computevm-runner"},
    }
    for resource in az("resource", "list", "-g", group):
        kind, name = resource["type"].lower(), resource["name"].lower()
        if kind == "microsoft.compute/disks" and (
            name.startswith("runner_osdisk_")
            or re.fullmatch(r"runner_disk1_[a-f0-9]{32}", name)
        ):
            continue
        if name not in allowed.get(kind, set()):
            raise ValueError(
                "Unexpected resource in execution scope; inspect before retirement"
            )
    template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [],
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "empty.json"
        path.write_text(json.dumps(template))
        path.chmod(0o600)
        az(
            "deployment",
            "group",
            "create",
            "-g",
            group,
            "-n",
            "retire-execution",
            "--mode",
            "Complete",
            "--template-file",
            str(path),
        )
    if az("resource", "list", "-g", group):
        raise RuntimeError("Execution resources remain in the retained group")


def stop(value, only_run=None):
    group = value["group"]
    if az("group", "exists", "-n", group):
        owned(group)
        if (
            only_run
            and az("group", "show", "-n", group).get("tags", {}).get("execution_run")
            != only_run
        ):
            raise ValueError("Failed start cannot retire another workflow's lease")
        # A token-only test cannot enumerate runner registrations. Remove the
        # registration inside the VM before deleting it instead.
        if not os.environ.get("GH_TOKEN") and os.environ.get("RUNNER_REMOVAL_TOKEN"):
            vms = az("vm", "list", "-g", group)
            if vms:
                protected_start(
                    value, os.environ["RUNNER_REMOVAL_TOKEN"], 0, stopping=True
                )
                # ARM PUT can be asynchronous; wait for the managed command.
                for _ in range(30):
                    result = az(
                        "vm",
                        "run-command",
                        "show",
                        "-g",
                        group,
                        "--vm-name",
                        "runner",
                        "--run-command-name",
                        "retire",
                        "--expand",
                        "instanceView",
                    )
                    state = result.get("instanceView", {}).get("executionState")
                    if state == "Succeeded":
                        break
                    if state in ("Failed", "TimedOut", "Canceled"):
                        raise RuntimeError("Runner deregistration failed")
                    time.sleep(5)
                else:
                    raise RuntimeError("Runner deregistration timed out")
        network_links(value, remove=True)
        if value.get("retain_group", False):
            retire_resources(value)
        else:
            az("group", "delete", "-n", group, "--yes")
    if not value.get("retain_group", False) and az("group", "exists", "-n", group):
        raise RuntimeError("Execution group still exists")
    name = "odyn-" + group.rsplit("-", 1)[1]
    runners = (
        json.loads(
            run(
                [
                    "gh",
                    "api",
                    f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runners",
                ]
            )
        )["runners"]
        if os.environ.get("GH_TOKEN")
        else []
    )
    for runner in runners:
        if runner["name"] == name:
            run(
                [
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runners/{runner['id']}",
                ]
            )
    print(
        json.dumps(
            {
                "schema": 1,
                "status": "retained_empty"
                if value.get("retain_group", False)
                else "absent",
                "group": group,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument(
        "--only-run", help="Failure cleanup may retire only this workflow's lease"
    )
    args = parser.parse_args()
    value = config()
    identity()
    if args.action == "start":
        start(value)
    else:
        stop(value, only_run=args.only_run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired):
        raise SystemExit(
            "Execution operation stopped. Inspect protected Azure diagnostics; use stop to retire partial setup."
        ) from None
