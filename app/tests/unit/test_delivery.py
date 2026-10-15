"""Delivery safeguards: isolated tests, no Azure access or publication."""

import base64
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("delivery", ROOT / "scripts/delivery.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DeliveryTests(unittest.TestCase):
    def release(self):
        return {
            "schema": 1,
            "source_sha": "a" * 40,
            "images": {
                name: {
                    "reference": f"demo.azurecr.io/{repo}@sha256:" + "b" * 64,
                    "local_id": "sha256:" + "c" * 64,
                }
                for name, repo in (
                    ("gateway", "odyn-gateway"),
                    ("collector", "odyn-collector"),
                )
            },
        }

    def plan(self, actions=None):
        return {
            "applyable": True,
            "checks": [{"status": "pass"}],
            "resource_changes": [
                {
                    "type": "azurerm_resource_group",
                    "address": "group",
                    "change": {"actions": ["no-op"], "after": {"name": "dedicated"}},
                },
                {
                    "type": "azurerm_container_app",
                    "address": "app",
                    "change": {
                        "actions": actions or ["update"],
                        "after": {"resource_group_name": "dedicated"},
                    },
                },
            ],
        }

    def test_release_requires_exact_source(self):
        with self.assertRaises(ValueError):
            MODULE.validate_release(self.release(), "wrong", "demo.azurecr.io")

    def test_release_rejects_tag_and_other_registry(self):
        for value in (
            "demo.azurecr.io/odyn-gateway:latest",
            "other.azurecr.io/odyn-gateway@sha256:" + "b" * 64,
        ):
            record = self.release()
            record["images"]["gateway"]["reference"] = value
            with self.assertRaises(ValueError):
                MODULE.validate_release(record, "a" * 40, "demo.azurecr.io")

    def test_valid_release_preserves_distinct_manifest_and_local_ids(self):
        record = MODULE.validate_release(self.release(), "a" * 40, "demo.azurecr.io")
        self.assertNotEqual(
            record["images"]["gateway"]["reference"].split("@")[-1],
            record["images"]["gateway"]["local_id"],
        )

    def test_destroy_and_replacement_are_rejected(self):
        for actions in (["delete"], ["delete", "create"]):
            with self.assertRaises(ValueError):
                MODULE.plan_summary(self.plan(actions), "dedicated")

    def test_clean_noop_plan_is_accepted(self):
        plan = self.plan()
        plan["applyable"] = False
        plan["resource_changes"][1]["change"]["actions"] = ["no-op"]
        self.assertEqual(len(MODULE.plan_summary(plan, "dedicated")), 2)

    def test_wrong_group_is_rejected(self):
        plan = self.plan()
        plan["resource_changes"][1]["change"]["after"]["resource_group_name"] = "shared"
        with self.assertRaises(ValueError):
            MODULE.plan_summary(plan, "dedicated")

    def test_failed_or_unknown_plan_checks_are_rejected(self):
        for status in ("fail", "unknown", "error"):
            plan = self.plan()
            plan["checks"] = [{"status": status}]
            with self.assertRaises(ValueError):
                MODULE.plan_summary(plan, "dedicated")

    def test_plan_review_summary_excludes_attribute_values(self):
        plan = self.plan()
        plan["resource_changes"][1]["change"]["after"]["secret"] = "SENTINEL_SECRET"
        self.assertNotIn(
            "SENTINEL_SECRET", json.dumps(MODULE.plan_summary(plan, "dedicated"))
        )

    def test_encrypt_plan_then_recover_exact_bytes(self):
        self.bundle_check()

    def test_expired_plan_is_rejected(self):
        self.bundle_check(age=3601, expected=ValueError)

    def test_changed_principal_is_rejected(self):
        self.bundle_check(changed=True, expected=ValueError)

    def test_modified_encrypted_artifact_is_rejected(self):
        self.bundle_check(tampered=True, expected=InvalidTag)

    def test_publish_first_release_to_empty_registry(self):
        self.publication_check()

    def test_publish_stops_when_remote_image_is_different(self):
        self.publication_check(mismatch=True)

    def publication_check(self, mismatch=False):
        calls = []
        image_id = "sha256:" + "a" * 64

        def run(args):
            calls.append(args)
            if args[:3] == ["docker", "image", "inspect"]:
                if mismatch and "@" in args[3]:
                    return "sha256:" + "c" * 64
                return image_id
            if args[:4] == ["az", "acr", "repository", "list"]:
                return "[]"
            if args[:4] == ["az", "acr", "repository", "show"]:
                return "sha256:" + "b" * 64
            return ""

        metadata = [
            {
                "RepoTags": [f"{repo}:release"],
                "Id": image_id,
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {"User": "10001:10001"},
            }
            for repo in ("odyn-gateway", "odyn-collector")
        ]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "ACR_NAME": "demo",
                    "ACR_LOGIN_SERVER": "demo.azurecr.io",
                    "GITHUB_SHA": "a" * 40,
                    "GITHUB_RUN_ID": "10",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
            ),
            patch.object(MODULE, "run", side_effect=run),
        ):
            directory = Path(temp)
            (directory / "images.json").write_text(json.dumps(metadata))
            if mismatch:
                with self.assertRaises(ValueError):
                    MODULE.publication(directory)
                self.assertFalse((directory / "release.json").exists())
                self.assertEqual(
                    sum(call[:2] == ["docker", "push"] for call in calls), 1
                )
            else:
                MODULE.publication(directory)
                self.assertEqual(
                    sum(call[:2] == ["docker", "push"] for call in calls), 2
                )
                record = json.loads((directory / "release.json").read_text())
                self.assertIn("@sha256:", record["images"]["gateway"]["reference"])

    def bundle_check(self, age=10, changed=False, tampered=False, expected=None):
        encryption_key = b"x" * 32
        context = {"source_sha": "source", "principal": "publisher"}
        data = b"private terraform state and plan"
        receipt = context | {
            "schema": 1,
            "created": time.time() - age,
            "plan_sha256": MODULE.sha256(data),
        }
        payload = json.dumps(
            {"receipt": receipt, "plan": base64.b64encode(data).decode()}
        ).encode()
        nonce = b"n" * 12
        ciphertext = nonce + AESGCM(encryption_key).encrypt(
            nonce, payload, b"odyn-plan-v1"
        )
        if tampered:
            ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {"PLAN_ENCRYPTION_KEY": base64.b64encode(encryption_key).decode()},
            ),
            patch.object(
                MODULE,
                "context",
                return_value=context | ({"principal": "other"} if changed else {}),
            ),
        ):
            directory = Path(temp)
            (directory / "plan.enc").write_bytes(ciphertext)
            if expected:
                with self.assertRaises(expected):
                    MODULE.unseal(directory)
                self.assertFalse((directory / "application.tfplan").exists())
            else:
                MODULE.unseal(directory)
                self.assertEqual((directory / "application.tfplan").read_bytes(), data)
