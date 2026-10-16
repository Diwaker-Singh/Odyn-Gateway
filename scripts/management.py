"""Idempotent ARM bootstrap for durable automation state, using Entra OIDC.

No Terraform backend exists yet at this boundary. ARM's deployment record and
source template own this small foundation. It never deletes or imports state.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def command(args):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300, check=False
    )
    if result.returncode:
        raise RuntimeError("Management bootstrap failed; inspect Azure privately")
    return result.stdout


def configuration():
    value = json.loads(os.environ["MANAGEMENT_CONFIG_JSON"])
    if not re.fullmatch(
        r"rg-odyn-gateway-automation-[a-z0-9-]+", value["resource_group"]
    ):
        raise ValueError("Use a dedicated automation group")
    if not re.fullmatch(r"[a-z0-9]{3,24}", value["storage_account"]):
        raise ValueError("Invalid storage account name")
    if not re.fullmatch(r"[a-z0-9-]{3,63}", value["container"]):
        raise ValueError("Invalid container name")
    if not re.fullmatch(r"[a-z0-9]+", value["location"]):
        raise ValueError("Invalid location")
    if not value.get("subnet_ids") or not value.get("principal_ids"):
        raise ValueError("Explicit private connectivity and state principals required")
    for principal in value["principal_ids"]:
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", principal):
            raise ValueError("Invalid state principal")
    for subnet in value["subnet_ids"]:
        prefix = f"/subscriptions/{os.environ['AZURE_SUBSCRIPTION_ID']}/resourceGroups/"
        if (
            not subnet.startswith(prefix)
            or "/providers/Microsoft.Network/virtualNetworks/" not in subnet
            or "/subnets/" not in subnet
        ):
            raise ValueError("State subnet must be in the configured subscription")
    return value


def template(value):
    account = value["storage_account"]
    resources = [
        {
            "type": "Microsoft.Storage/storageAccounts",
            "apiVersion": "2023-05-01",
            "name": account,
            "location": value["location"],
            "kind": "StorageV2",
            "sku": {"name": "Standard_LRS"},
            "tags": {
                "project": "odyn-gateway",
                "purpose": "automation-state",
                "managed_by": "odyn-management-pipeline",
            },
            "properties": {
                "minimumTlsVersion": "TLS1_2",
                "supportsHttpsTrafficOnly": True,
                "allowSharedKeyAccess": False,
                "allowBlobPublicAccess": False,
                "defaultToOAuthAuthentication": True,
                "publicNetworkAccess": "Enabled",
                "networkAcls": {
                    "defaultAction": "Deny",
                    "bypass": "None",
                    "ipRules": [],
                    "virtualNetworkRules": [
                        {"id": s, "action": "Allow"} for s in value["subnet_ids"]
                    ],
                },
            },
        },
        {
            "type": "Microsoft.Storage/storageAccounts/blobServices",
            "apiVersion": "2023-05-01",
            "name": f"{account}/default",
            "dependsOn": [
                f"[resourceId('Microsoft.Storage/storageAccounts', '{account}')]"
            ],
            "properties": {
                "isVersioningEnabled": True,
                "deleteRetentionPolicy": {"enabled": True, "days": 7},
                "containerDeleteRetentionPolicy": {"enabled": True, "days": 7},
            },
        },
        {
            "type": "Microsoft.Storage/storageAccounts/blobServices/containers",
            "apiVersion": "2023-05-01",
            "name": f"{account}/default/{value['container']}",
            "dependsOn": [
                f"[resourceId('Microsoft.Storage/storageAccounts/blobServices', '{account}', 'default')]"
            ],
            "properties": {"publicAccess": "None"},
        },
    ]
    for principal in value["principal_ids"]:
        resources.append(
            {
                "type": "Microsoft.Authorization/roleAssignments",
                "apiVersion": "2022-04-01",
                "name": f"[guid(resourceId('Microsoft.Storage/storageAccounts', '{account}'), '{principal}', 'blob-state')]",
                "scope": f"[resourceId('Microsoft.Storage/storageAccounts', '{account}')]",
                "dependsOn": [
                    f"[resourceId('Microsoft.Storage/storageAccounts', '{account}')]"
                ],
                "properties": {
                    "principalId": principal,
                    "roleDefinitionId": "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')]",
                },
            }
        )
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": resources,
    }


def provision():
    value = configuration()
    account = json.loads(command(["az", "account", "show", "-o", "json"]))
    if (
        account.get("id") != os.environ["AZURE_SUBSCRIPTION_ID"]
        or account.get("tenantId") != os.environ["AZURE_TENANT_ID"]
        or account.get("user", {}).get("type") != "servicePrincipal"
        or account.get("user", {}).get("name", "").lower()
        != os.environ["AZURE_CLIENT_ID"].lower()
    ):
        raise ValueError("Management identity/destination mismatch")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Management bootstrap requires protected main")
    group = value["resource_group"]
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    suffix = ["--subscription", subscription, "--only-show-errors", "-o", "json"]
    if json.loads(command(["az", "group", "exists", "-n", group, *suffix])):
        metadata = json.loads(command(["az", "group", "show", "-n", group, *suffix]))
        if metadata.get("tags", {}).get("managed_by") != "odyn-management-pipeline":
            raise ValueError(
                "Existing management group needs explicit administrative adoption"
            )
    else:
        command(
            [
                "az",
                "group",
                "create",
                "-n",
                group,
                "-l",
                value["location"],
                "--tags",
                "project=odyn-gateway",
                "managed_by=odyn-management-pipeline",
                *suffix,
            ]
        )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "management.json"
        path.write_text(json.dumps(template(value)))
        os.chmod(path, 0o600)
        command(
            [
                "az",
                "deployment",
                "group",
                "create",
                "--name",
                "odyn-management-state",
                "--resource-group",
                group,
                "--mode",
                "Incremental",
                "--template-file",
                str(path),
                *suffix,
            ]
        )
    print(
        "Management state provisioned; existing state blobs were preserved. No deployment resources were removed."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Provision management storage; costs require approval",
    )
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.execute:
            provision()
        else:
            configuration()
            print("Configuration valid; no Azure operations performed.")
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ):
        print(
            "Management bootstrap stopped; inspect private configuration and Azure deployment status."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
