"""State recovery authenticates backups and refuses destructive backend writes."""

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.exceptions import InvalidTag

SPEC = importlib.util.spec_from_file_location(
    "state_recovery", Path(__file__).resolve().parents[3] / "scripts/state-recovery.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class StateRecoveryTests(unittest.TestCase):
    def test_cli_children_do_not_receive_recovery_secrets(self):
        with (
            patch.dict(os.environ, {"ENCRYPTED_RECOVERY_B64": "opaque"}),
            patch.object(
                M.subprocess, "run", return_value=SimpleNamespace(stdout="{}")
            ) as run,
        ):
            M.command(["az", "account", "show"])
        passed = run.call_args.kwargs["env"]
        self.assertNotIn("RECOVERY_ENCRYPTION_KEY", passed)
        self.assertNotIn("ENCRYPTED_RECOVERY_B64", passed)
        self.assertIn("AZURE_SUBSCRIPTION_ID", passed)

    def setUp(self):
        self.subscription = "a" * 36
        self.tenant = "b" * 36
        self.client = "c" * 36
        self.backends = {
            label: {
                "resource_group_name": "rg-odyn-gateway-tfstate",
                "storage_account_name": "odynstate123",
                "container_name": "tfstate",
                "key": label + ".tfstate",
                "subscription_id": self.subscription,
                "tenant_id": self.tenant,
                "use_azuread_auth": True,
            }
            for label in M.LABELS
        }
        self.env = {
            "RECOVERY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
            "AZURE_SUBSCRIPTION_ID": self.subscription,
            "AZURE_TENANT_ID": self.tenant,
            "AZURE_CLIENT_ID": self.client,
            "GITHUB_REF": "refs/heads/main",
            "STATE_BACKENDS_JSON": json.dumps(self.backends),
        }
        active = patch.dict(os.environ, self.env)
        active.start()
        self.addCleanup(active.stop)
        self.state = json.dumps(
            {
                "version": 4,
                "serial": 12,
                "lineage": "12345678-1234-1234-1234-123456789abc",
                "resources": [{"type": "azurerm_resource_group", "instances": []}],
            }
        ).encode()
        self.payload = {
            "schema": 1,
            "subscription": self.subscription,
            "states": {k: base64.b64encode(self.state).decode() for k in M.LABELS},
        }

    def archive(self):
        raw = M.seal(self.payload, self.subscription)
        return raw, hashlib.sha256(raw).hexdigest()

    def test_round_trip_preserves_exact_bytes_for_all_states(self):
        raw, digest = self.archive()
        result = M.open_archive(raw, digest, self.subscription)
        self.assertEqual(result, {k: self.state for k in M.LABELS})
        self.assertNotIn(self.state, raw)

    def test_wrong_digest_rejected(self):
        raw, _ = self.archive()
        with self.assertRaises(ValueError):
            M.open_archive(raw, "0" * 64, self.subscription)

    def test_tamper_rejected_even_with_matching_ciphertext_digest(self):
        raw, _ = self.archive()
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
        with self.assertRaises(InvalidTag):
            M.open_archive(raw, hashlib.sha256(raw).hexdigest(), self.subscription)

    def test_wrong_key_rejected(self):
        raw, digest = self.archive()
        with (
            patch.dict(
                os.environ,
                {"RECOVERY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode()},
            ),
            self.assertRaises(InvalidTag),
        ):
            M.open_archive(raw, digest, self.subscription)

    def test_wrong_subscription_rejected(self):
        raw, digest = self.archive()
        with self.assertRaises(ValueError):
            M.open_archive(raw, digest, "foreign")

    def test_missing_state_and_invalid_serial_rejected(self):
        del self.payload["states"]["registry"]
        with self.assertRaises(ValueError):
            M.seal(self.payload, self.subscription)
        self.payload["states"]["registry"] = base64.b64encode(
            json.dumps({"version": 4, "serial": True, "resources": [{}]}).encode()
        ).decode()
        with self.assertRaises(ValueError):
            M.seal(self.payload, self.subscription)

    def test_foreign_resource_id_rejected(self):
        state = json.loads(self.state)
        state["resources"][0]["instances"] = [
            {
                "attributes": {
                    "id": "/subscriptions/foreign/resourceGroups/rg-odyn-gateway-test"
                }
            }
        ]
        self.payload["states"]["application"] = base64.b64encode(
            json.dumps(state).encode()
        ).decode()
        with self.assertRaises(ValueError):
            M.seal(self.payload, self.subscription)

    def test_excessive_decompression_rejected(self):
        raw, digest = self.archive()
        with patch.object(M, "MAX_PLAIN", len(raw) + 1), self.assertRaises(ValueError):
            M.open_archive(raw, digest, self.subscription)

    def fake_azure(self, *args):
        if args[:2] == ("account", "show"):
            return {
                "id": self.subscription,
                "tenantId": self.tenant,
                "user": {"type": "servicePrincipal", "name": self.client},
            }
        if args[:3] == ("storage", "account", "show"):
            return {
                "id": (
                    f"/subscriptions/{self.subscription}"
                    "/resourceGroups/rg-odyn-gateway-tfstate"
                    "/providers/Microsoft.Storage/storageAccounts/odynstate123"
                ),
                "allowSharedKeyAccess": False,
                "allowBlobPublicAccess": False,
                "networkRuleSet": {"defaultAction": "Deny"},
                "minimumTlsVersion": "TLS1_2",
                "enableHttpsTrafficOnly": True,
            }
        if args[:3] == ("storage", "blob", "exists"):
            return {"exists": False}
        if args[:3] == ("storage", "blob", "upload"):
            self.assertIn("--if-none-match", args)
            self.assertEqual(args[args.index("--if-none-match") + 1], "*")
            self.assertEqual(args[args.index("--overwrite") + 1], "false")
            self.assertEqual(
                Path(args[args.index("--file") + 1]).stat().st_mode & 0o777, 0o600
            )
            return {}
        if args[:3] == ("storage", "blob", "download"):
            Path(args[args.index("--file") + 1]).write_bytes(self.state)
            return {}
        raise AssertionError("Unexpected cloud operation")

    def restore(self, component="foundation"):
        return M.restore(
            {k: self.state for k in M.LABELS},
            component,
            f"restore {self.subscription} {component}",
        )

    def test_foundation_restores_two_states_with_atomic_create_and_readback(self):
        with patch.object(M, "az", side_effect=self.fake_azure) as az:
            self.assertEqual(self.restore(), ["bootstrap", "registry"])
        calls = [c.args[:3] for c in az.call_args_list]
        self.assertEqual(calls.count(("storage", "blob", "upload")), 2)
        self.assertLess(
            max(i for i, c in enumerate(calls) if c == ("storage", "blob", "exists")),
            min(i for i, c in enumerate(calls) if c == ("storage", "blob", "upload")),
        )

    def test_existing_or_unknown_blob_blocks_every_upload(self):
        for exists in (True, None):

            def azure(*args):
                return (
                    {"exists": exists}
                    if args[:3] == ("storage", "blob", "exists")
                    else self.fake_azure(*args)
                )

            with (
                self.subTest(exists=exists),
                patch.object(M, "az", side_effect=azure) as az,
                self.assertRaises(ValueError),
            ):
                self.restore()
            self.assertFalse(
                any(
                    c.args[:3] == ("storage", "blob", "upload")
                    for c in az.call_args_list
                )
            )

    def test_storage_error_never_means_missing_state(self):
        with (
            patch.object(M, "az", side_effect=subprocess.SubprocessError),
            self.assertRaises(subprocess.SubprocessError),
        ):
            self.restore()

    def test_wrong_confirmation_and_branch_fail_before_azure(self):
        with patch.object(M, "az") as az, self.assertRaises(ValueError):
            M.restore({}, "application", "wrong")
        az.assert_not_called()
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/other"}),
            patch.object(M, "az") as az,
            self.assertRaises(ValueError),
        ):
            self.restore()
        az.assert_not_called()

    def test_wrong_identity_cannot_write(self):
        with (
            patch.object(M, "az", return_value={"id": "other"}) as az,
            self.assertRaises(ValueError),
        ):
            self.restore()
        self.assertEqual(az.call_count, 1)

    def test_credentials_and_duplicate_destinations_rejected(self):
        self.backends["bootstrap"]["access_key"] = "forbidden-placeholder"
        with (
            patch.dict(os.environ, {"STATE_BACKENDS_JSON": json.dumps(self.backends)}),
            self.assertRaises(ValueError),
        ):
            M.checked_backends("foundation", self.subscription)
        del self.backends["bootstrap"]["access_key"]
        self.backends["registry"]["key"] = self.backends["bootstrap"]["key"]
        with (
            patch.dict(os.environ, {"STATE_BACKENDS_JSON": json.dumps(self.backends)}),
            self.assertRaises(ValueError),
        ):
            M.checked_backends("foundation", self.subscription)

    def test_wrong_readback_fails(self):
        def azure(*args):
            result = self.fake_azure(*args)
            if args[:3] == ("storage", "blob", "download"):
                Path(args[args.index("--file") + 1]).write_bytes(b"wrong")
            return result

        with patch.object(M, "az", side_effect=azure), self.assertRaises(ValueError):
            self.restore("application")

    def test_workflow_event_archive_is_validated_without_azure(self):
        raw, digest = self.archive()
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            target = Path(directory) / "recovery.enc"
            event.write_text(
                json.dumps(
                    {"inputs": {"encrypted_snapshot": base64.b64encode(raw).decode()}}
                )
            )
            with (
                patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}),
                patch(
                    "sys.argv",
                    [
                        "state-recovery",
                        "archive",
                        "--archive",
                        str(target),
                        "--sha256",
                        digest,
                    ],
                ),
                patch.object(M, "az") as az,
            ):
                M.main()
            az.assert_not_called()
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_unprotected_backend_is_rejected_before_upload(self):
        def azure(*args):
            value = self.fake_azure(*args)
            if args[:3] == ("storage", "account", "show"):
                value["networkRuleSet"]["defaultAction"] = "Allow"
            return value

        with (
            patch.object(M, "az", side_effect=azure) as az,
            self.assertRaises(ValueError),
        ):
            self.restore()
        self.assertFalse(
            any(c.args[:3] == ("storage", "blob", "upload") for c in az.call_args_list)
        )
