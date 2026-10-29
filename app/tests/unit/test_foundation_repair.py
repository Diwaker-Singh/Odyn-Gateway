"""A retained ARM deployment record must not hide a deleted state container."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "foundation_delivery", ROOT / "scripts/delivery.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FoundationRepairTests(unittest.TestCase):
    def plan(self, responses, component="bootstrap"):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "application.tfvars.json").write_text(
                json.dumps({"storage_account_name": "statetest"})
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "ODYN_INFRA_COMPONENT": component,
                        "AZURE_RESOURCE_GROUP": "rg-odyn-gateway-tfstate",
                    },
                ),
                patch.object(M, "context", return_value={"subscription": "sub"}),
                patch.object(M, "infrastructure_root", return_value=directory / "root"),
                patch.object(M, "run", side_effect=responses) as run,
            ):
                M.plan_infrastructure(directory)
                return [call.args[0] for call in run.call_args_list]

    def test_missing_child_container_forces_only_its_template_deployment(self):
        calls = self.plan(["true", '[{"name":"statetest"}]', '{"value":[]}', "plan"])
        self.assertEqual(
            calls[-1][-1],
            "-replace=azurerm_resource_group_template_deployment.container",
        )
        self.assertFalse(any(command[:2] == ["az", "deployment"] for command in calls))

    def test_existing_container_never_forces_replacement(self):
        calls = self.plan(
            ["true", '[{"name":"statetest"}]', '{"value":[{"name":"tfstate"}]}', "plan"]
        )
        self.assertFalse(any(arg.startswith("-replace") for arg in calls[-1]))

    def test_new_group_or_account_uses_normal_creation(self):
        for responses in (["false", "plan"], ["true", "[]", "plan"]):
            with self.subTest(responses=responses):
                calls = self.plan(responses)
                self.assertFalse(any(arg.startswith("-replace") for arg in calls[-1]))

    def test_container_read_denied_is_never_treated_as_absent(self):
        error = subprocess.CalledProcessError(1, ["az", "rest"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.plan(["true", '[{"name":"statetest"}]', error])

    def test_invalid_container_inventory_fails_closed(self):
        with self.assertRaises(TypeError):
            self.plan(["true", '[{"name":"statetest"}]', '{"value":null}'])

    def test_registry_plan_does_not_inspect_or_replace_state_container(self):
        calls = self.plan(["plan"], component="registry")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "terraform")
