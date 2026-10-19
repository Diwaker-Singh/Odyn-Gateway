"""Management storage bootstrap boundaries and secure template defaults."""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "management", ROOT / "scripts/management.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ManagementTests(unittest.TestCase):
    def config(self):
        return {
            "resource_group": "rg-odyn-gateway-automation-state",
            "storage_account": "odynstate123",
            "container": "management",
            "location": "centralindia",
            "principal_ids": ["a" * 36],
            "subnet_ids": [
                "/subscriptions/sub/resourceGroups/runner/providers/Microsoft.Network/virtualNetworks/runner/subnets/private"
            ],
        }

    def test_template_uses_deny_encryption_identity_and_retention(self):
        resources = M.template(self.config())["resources"]
        account = resources[0]["properties"]
        self.assertFalse(account["allowSharedKeyAccess"])
        self.assertFalse(account["allowBlobPublicAccess"])
        self.assertTrue(account["supportsHttpsTrafficOnly"])
        self.assertEqual(account["networkAcls"]["defaultAction"], "Deny")
        self.assertEqual(account["networkAcls"]["ipRules"], [])
        self.assertTrue(resources[1]["properties"]["isVersioningEnabled"])
        self.assertEqual(resources[2]["properties"]["publicAccess"], "None")
        self.assertNotIn("Owner", json.dumps(resources))

    def test_rejects_unrelated_group_and_missing_private_connectivity(self):
        for field, value in (
            ("resource_group", "shared"),
            ("subnet_ids", []),
            ("principal_ids", []),
            ("storage_account", "../other"),
        ):
            config = self.config() | {field: value}
            with (
                patch.dict(
                    os.environ,
                    {
                        "MANAGEMENT_CONFIG_JSON": json.dumps(config),
                        "AZURE_SUBSCRIPTION_ID": "sub",
                    },
                ),
                self.assertRaises(ValueError),
            ):
                M.configuration()

    def test_rejects_subnet_in_another_subscription(self):
        config = self.config()
        config["subnet_ids"] = [config["subnet_ids"][0].replace("/sub/", "/other/")]
        with (
            patch.dict(
                os.environ,
                {
                    "MANAGEMENT_CONFIG_JSON": json.dumps(config),
                    "AZURE_SUBSCRIPTION_ID": "sub",
                },
            ),
            self.assertRaises(ValueError),
        ):
            M.configuration()

    def test_wrong_identity_stops_before_writes(self):
        with (
            patch.object(M, "configuration", return_value=self.config()),
            patch.dict(
                os.environ,
                {
                    "AZURE_SUBSCRIPTION_ID": "sub",
                    "AZURE_TENANT_ID": "tenant",
                    "AZURE_CLIENT_ID": "client",
                },
            ),
            patch.object(
                M, "command", return_value=json.dumps({"id": "wrong"})
            ) as command,
        ):
            with self.assertRaises(ValueError):
                M.provision()
            self.assertEqual(command.call_count, 1)

    def test_management_workflow_cannot_run_on_push(self):
        workflow = (ROOT / ".github/workflows/management.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("default: true", workflow)
        self.assertIn(
            "environment: ${{ inputs.target == 'disposable' && "
            "'azure-test-management' || 'azure-management' }}",
            workflow,
        )
        self.assertIn("AZURE_MANAGEMENT_ENABLED", workflow)
        self.assertNotIn("  push:", workflow)
