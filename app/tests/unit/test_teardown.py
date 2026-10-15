"""Teardown guards are tested without Azure access or destructive commands."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("teardown", ROOT / "scripts/teardown.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TeardownTests(unittest.TestCase):
    def driver(self, run=lambda _: ""):
        return MODULE.Teardown(
            "sub", "tenant", "demo10", "rg-odyn-gateway-tfstate", run
        )

    def test_mismatched_account_stops_before_resource_queries(self):
        calls = []

        def run(args):
            calls.append(args)
            return json.dumps({"id": "wrong", "tenantId": "tenant"})

        with self.assertRaises(ValueError):
            self.driver(run).inspect()
        self.assertEqual(len(calls), 1)

    def test_rejects_arbitrary_groups_and_suffixes(self):
        for env, group in [
            ("../other", "rg-odyn-gateway-tfstate"),
            ("demo10", "shared"),
        ]:
            with self.assertRaises(ValueError):
                MODULE.Teardown("sub", "tenant", env, group)

    def test_preview_reads_only(self):
        calls = []

        def run(args):
            calls.append(args)
            if args[1:3] == ["account", "show"]:
                return json.dumps({"id": "sub", "tenantId": "tenant"})
            return "false"

        self.driver(run).inspect()
        self.assertFalse(any("delete" in call for call in calls))

    def test_state_recovery_failure_prevents_all_deletion(self):
        driver = self.driver(
            lambda _: (_ for _ in ()).throw(RuntimeError("unavailable"))
        )
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(driver, "inspect", return_value={}),
            patch.object(driver, "exists", return_value=True),
            patch.object(driver, "delete") as delete,
        ):
            with self.assertRaises(RuntimeError):
                driver.execute(Path(temp) / "backup")
            delete.assert_not_called()

    def test_never_initialized_backend_with_absent_app_allows_cleanup(self):
        driver = self.driver()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(driver, "inspect", return_value={}),
            patch.object(driver, "exists", return_value=False),
            patch.object(driver, "delete") as delete,
            patch.object(MODULE, "ROOT", Path(temp)),
        ):
            driver.execute(Path(temp) / "backup")
            self.assertEqual(
                [call.args[0] for call in delete.call_args_list], driver.groups
            )
            self.assertEqual(
                (Path(temp) / "backup/inventory.json").stat().st_mode & 0o777, 0o600
            )

    def test_failed_application_delete_retains_registry_and_state(self):
        driver = self.driver()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(driver, "inspect", return_value={}),
            patch.object(driver, "exists", return_value=False),
            patch.object(
                driver, "delete", side_effect=RuntimeError("blocked")
            ) as delete,
        ):
            with self.assertRaises(RuntimeError):
                driver.execute(Path(temp) / "backup")
            self.assertEqual(delete.call_count, 1)

    def test_unverified_managed_group_stops(self):
        def run(args):
            if args[1:3] == ["account", "show"]:
                return json.dumps({"id": "sub", "tenantId": "tenant"})
            if args[1:3] == ["group", "exists"]:
                return "true"
            if args[1:3] == ["group", "show"]:
                return json.dumps({"tags": {"managed_by": "terraform"}})
            return "[]"

        with self.assertRaisesRegex(ValueError, "ownership"):
            self.driver(run).inspect()
