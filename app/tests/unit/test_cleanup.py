"""Cleanup approval and recovery regressions; no cloud requests or deletion."""

import base64
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("cleanup", ROOT / "scripts/cleanup.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.d = Mock(subscription="sub", tenant="tenant", app="rg-odyn-gateway-demo10")
        self.d.groups = [
            self.d.app,
            self.d.app + "-managed",
            "rg-odyn-gateway-artifacts-demo10",
            "rg-odyn-gateway-tfstate",
        ]
        self.d.inspect.return_value = {
            g: [{"id": g + "/one", "type": "Resource/Kind"}] for g in self.d.groups
        }
        self.d.exists.return_value = False
        self.context = {
            "source": "a" * 40,
            "run": "123",
            "subscription": "sub",
            "tenant": "tenant",
            "principal": "client",
            "groups": self.d.groups,
        }
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.env = {
            "PLAN_ENCRYPTION_KEY": base64.b64encode(self.key).decode(),
            "CLEANUP_CONFIRMATION": "delete sub rg-odyn-gateway-demo10",
            "GITHUB_STEP_SUMMARY": str(self.directory / "summary"),
        }
        self.addCleanup(self.temp.cleanup)

    def bundle(self, **updates):
        receipt = (
            self.context
            | {
                "schema": 1,
                "created": int(time.time()),
                "inventory": M.inventory_identity(self.d.inspect()),
            }
            | updates
        )
        nonce = b"n" * 12
        (self.directory / "recovery.enc").write_bytes(
            nonce
            + AESGCM(self.key).encrypt(
                nonce,
                json.dumps({"receipt": receipt, "states": {}}).encode(),
                b"odyn-cleanup-v1",
            )
        )

    def execute(self):
        with (
            patch.dict(os.environ, self.env),
            patch.object(M, "driver", return_value=self.d),
            patch.object(M, "binding", return_value=self.context),
        ):
            M.execute(self.directory)

    def test_valid_bundle_deletes_in_order_and_verifies_absence(self):
        self.bundle()
        self.execute()
        self.assertEqual(
            [c.args[0] for c in self.d.delete.call_args_list], self.d.groups
        )
        self.assertEqual(self.d.exists.call_count, 4)

    def test_expired_approval_prevents_deletion(self):
        self.bundle(created=int(time.time()) - 3601)
        with self.assertRaises(ValueError):
            self.execute()
        self.d.delete.assert_not_called()

    def test_other_source_run_or_principal_prevents_deletion(self):
        for field in ("source", "run", "principal", "subscription"):
            self.bundle(**{field: "different"})
            with self.assertRaises(ValueError):
                self.execute()
        self.d.delete.assert_not_called()

    def test_changed_inventory_prevents_deletion(self):
        self.bundle()
        self.d.inspect.return_value[self.d.app].append(
            {"id": "new", "type": "Resource/Kind"}
        )
        with self.assertRaises(ValueError):
            self.execute()
        self.d.delete.assert_not_called()

    def test_confirmation_required(self):
        self.bundle()
        self.env["CLEANUP_CONFIRMATION"] = ""
        with self.assertRaises(ValueError):
            self.execute()
        self.d.delete.assert_not_called()

    def test_tampering_prevents_deletion(self):
        self.bundle()
        p = self.directory / "recovery.enc"
        b = bytearray(p.read_bytes())
        b[-1] ^= 1
        p.write_bytes(b)
        with self.assertRaises(InvalidTag):
            self.execute()
        self.d.delete.assert_not_called()

    def test_failed_first_delete_stops_sequence(self):
        self.bundle()
        self.d.delete.side_effect = RuntimeError("blocked")
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.d.delete.call_count, 1)

    def test_absence_failure_is_not_success(self):
        self.bundle()
        self.d.exists.return_value = True
        with self.assertRaises(RuntimeError):
            self.execute()

    def test_recovery_is_durable_and_plaintext_removed(self):
        app = {
            "resource_group_name": self.d.groups[3],
            "storage_account_name": "appstate",
            "container_name": "tfstate",
            "key": "dev.tfstate",
            "use_azuread_auth": True,
        }
        management = {
            **app,
            "resource_group_name": "rg-odyn-gateway-automation-state",
            "storage_account_name": "management",
            "key": self.d.groups[3] + "/bootstrap.tfstate",
        }
        registry = {**management, "key": self.d.groups[2] + "/registry.tfstate"}
        self.env.update(
            TF_BACKEND_JSON=json.dumps(app),
            BOOTSTRAP_BACKEND_JSON=json.dumps(management),
            REGISTRY_BACKEND_JSON=json.dumps(registry),
        )
        calls = []

        def run(args):
            calls.append(args)
            if "download" in args:
                Path(args[args.index("--file") + 1]).write_text(
                    json.dumps(
                        {
                            "resources": [],
                            "lineage": "lineage",
                            "private": "SECRET_MARKER",
                        }
                    )
                )
            return ""

        with (
            patch.dict(os.environ, self.env),
            patch.object(M, "driver", return_value=self.d),
            patch.object(M, "binding", return_value=self.context),
            patch.object(M.teardown, "command", side_effect=run),
        ):
            M.prepare(self.directory / "snapshot")
        self.assertEqual(sum("download" in c for c in calls), 3)
        self.assertEqual(sum("upload" in c for c in calls), 1)
        self.assertEqual(list((self.directory / "snapshot").glob("*.tfstate")), [])
        self.assertNotIn(
            b"SECRET_MARKER", (self.directory / "snapshot/recovery.enc").read_bytes()
        )
        self.assertNotIn("SECRET_MARKER", (self.directory / "summary").read_text())
        self.assertEqual(
            (self.directory / "snapshot/recovery.enc").stat().st_mode & 0o777, 0o600
        )
        self.d.delete.assert_not_called()

    def test_workflow_defaults_safe_and_requires_separate_approval(self):
        text = (ROOT / ".github/workflows/cleanup.yml").read_text()
        for required in (
            "default: true",
            "azure-cleanup-plan",
            "azure-cleanup-test",
            "AZURE_CLEANUP_ENABLED",
            "odyn-cleanup",
            "needs: snapshot",
            "inputs.confirmation",
        ):
            self.assertIn(required, text)
        for name in (
            "cleanup.yml",
            "infrastructure.yml",
            "deploy.yml",
            "management.yml",
        ):
            self.assertIn(
                "azure-resource-mutation",
                (ROOT / ".github/workflows" / name).read_text(),
            )
