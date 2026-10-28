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

    def test_init_diagnostics_are_reduced_to_safe_categories(self):
        cases = (
            ("Error acquiring the state lock", "TERRAFORM_STATE_LOCK"),
            (
                "Failed to get existing workspaces: listing blobs returned 403",
                "BACKEND_AUTHENTICATION",
            ),
            (
                "Failed to query available provider packages",
                "PROVIDER_REGISTRY_ACCESS",
            ),
            (
                "provider package checksum doesn't match any of the checksums",
                "PROVIDER_CHECKSUM",
            ),
            ("Error while installing provider", "PROVIDER_INSTALLATION"),
            ("failed to get backend configuration", "BACKEND_CONFIGURATION"),
            ("context deadline exceeded", "NETWORK_OR_PRIVATE_ENDPOINT"),
            ("opaque unclassified text", "TERRAFORM_INIT_OTHER"),
        )
        for content, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(MODULE.classify_init_failure(content), expected)

    def test_init_diagnostic_classification_never_returns_raw_log(self):
        sensitive = "storage account odynprivate123 connection secret=do-not-print"
        result = MODULE.classify_init_failure(sensitive)
        self.assertNotIn("odynprivate123", result)
        self.assertNotIn("do-not-print", result)

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


class FoundationTests(unittest.TestCase):
    def configuration(self, component="bootstrap"):
        group = (
            "rg-odyn-gateway-tfstate"
            if component == "bootstrap"
            else "rg-odyn-gateway-artifacts-demo10"
        )
        variables = {
            "subscription_id": "sub",
            "tenant_id": "tenant",
            "resource_group_name": group,
            "environment": "demo10",
        }
        backend = {
            "resource_group_name": "rg-management",
            "storage_account_name": "managementstore",
            "container_name": "tfstate",
            "key": f"{group}/{component}.tfstate",
            "use_azuread_auth": True,
        }
        return {
            "ODYN_INFRA_COMPONENT": component,
            "APPLICATION_TFVARS_JSON": json.dumps(variables),
            "TF_BACKEND_JSON": json.dumps(backend),
            "AZURE_RESOURCE_GROUP": group,
            "AZURE_SUBSCRIPTION_ID": "sub",
            "AZURE_TENANT_ID": "tenant",
        }

    def test_both_roots_preserve_lock_and_attach_remote_backend(self):
        for component, folder in (
            ("bootstrap", "bootstrap"),
            ("registry", "artifacts"),
        ):
            with (
                self.subTest(component=component),
                tempfile.TemporaryDirectory() as temp,
                patch.dict(os.environ, self.configuration(component)),
            ):
                directory = Path(temp)
                MODULE.prepare_infrastructure(directory)
                self.assertEqual(
                    (directory / "root/.terraform.lock.hcl").read_bytes(),
                    (ROOT / f"infra/{folder}/.terraform.lock.hcl").read_bytes(),
                )
                self.assertIn(
                    'backend "azurerm"', (directory / "root/backend.tf").read_text()
                )
                self.assertEqual(
                    MODULE.infrastructure_root(directory), directory / "root"
                )

    def test_management_state_cannot_be_in_target_group(self):
        self.reject_backend({"resource_group_name": "rg-odyn-gateway-tfstate"})

    def test_management_state_rejects_static_credentials(self):
        for name in ("access_key", "sas_token", "client_secret"):
            with self.subTest(name=name):
                self.reject_backend({name: "do-not-use"})
        self.reject_backend({"use_azuread_auth": False})

    def test_management_state_key_is_bound_to_group_and_component(self):
        self.reject_backend({"key": "application.tfstate"})
        self.reject_backend({"key": "rg-odyn-gateway-tfstate/registry.tfstate"})

    def test_management_state_rejects_cross_subscription_and_tenant(self):
        self.reject_backend({"subscription_id": "another"})
        self.reject_backend({"tenant_id": "another"})

    def test_unknown_component_cannot_select_arbitrary_terraform_source(self):
        with (
            patch.dict(os.environ, {"ODYN_INFRA_COMPONENT": "../../other"}),
            self.assertRaises(ValueError),
        ):
            MODULE.infrastructure_root(Path("unused"))

    def test_foundation_tfvars_destination_must_match(self):
        configuration = self.configuration()
        configuration["AZURE_RESOURCE_GROUP"] = "other"
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, configuration),
            self.assertRaises(ValueError),
        ):
            MODULE.prepare_infrastructure(Path(temp))

    def reject_backend(self, changes):
        configuration = self.configuration()
        backend = json.loads(configuration["TF_BACKEND_JSON"]) | changes
        configuration["TF_BACKEND_JSON"] = json.dumps(backend)
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, configuration),
            self.assertRaises(ValueError),
        ):
            MODULE.prepare_infrastructure(Path(temp))


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": "example/gateway",
                "ACR_LOGIN_SERVER": "demo.azurecr.io",
                "DELIVERY_TARGET": "disposable",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.ctx = {
            "inputs_sha256": "settings",
            "subscription": "sub",
            "tenant": "tenant",
            "resource_group": "dedicated",
            "registry": "demo.azurecr.io",
        }
        self.context = patch.object(MODULE, "context", return_value=self.ctx)
        self.context.start()
        self.addCleanup(self.context.stop)

    def record(self):
        return {
            "schema": 1,
            "deployment_run_id": "123",
            "deployment_source": "a" * 40,
            "target": "disposable",
            "context": self.ctx.copy(),
            "release": DeliveryTests().release(),
        }

    def metadata(self):
        return {
            "id": 123,
            "path": ".github/workflows/deploy.yml",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": "example/gateway"},
        }

    def test_verified_previous_deployment_is_accepted(self):
        MODULE.validate_deployment(self.record(), self.metadata(), "123")

    def test_wrong_run_source_repository_and_failed_run_are_rejected(self):
        for field, value in [
            ("id", 456),
            ("path", ".github/workflows/release.yml"),
            ("event", "pull_request"),
            ("head_branch", "feature"),
            ("head_sha", "b" * 40),
            ("status", "in_progress"),
            ("conclusion", "failure"),
            ("repository", {"full_name": "other/repo"}),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                MODULE.validate_deployment(
                    self.record(), self.metadata() | {field: value}, "123"
                )

    def test_other_profile_and_configuration_are_rejected(self):
        for record in [
            self.record() | {"target": "deployment"},
            self.record() | {"context": self.ctx | {"inputs_sha256": "different"}},
            self.record() | {"context": self.ctx | {"subscription": "other"}},
        ]:
            with self.assertRaises(ValueError):
                MODULE.validate_deployment(record, self.metadata(), "123")

    def plan(self):
        before = {
            "name": "app",
            "template": [
                {
                    "container": [
                        {"name": "gateway", "image": "old"},
                        {"name": "collector", "image": "collector"},
                    ]
                }
            ],
            "latest_revision_name": "old-revision",
        }
        after = json.loads(json.dumps(before))
        after["template"][0]["container"][0]["image"] = "new"
        after["latest_revision_name"] = None
        return {
            "resource_changes": [
                {
                    "type": "azurerm_container_app",
                    "change": {
                        "actions": ["update"],
                        "before": before,
                        "after": after,
                        "after_unknown": {"latest_revision_name": True},
                    },
                }
            ]
        }

    def test_image_only_update_and_known_computed_field_are_accepted(self):
        self.assertEqual(MODULE.rollback_plan(self.plan()), 1)

    def test_rollback_rejects_unrelated_configuration_changes(self):
        plan = self.plan()
        plan["resource_changes"][0]["change"]["after"]["name"] = "different"
        with self.assertRaises(ValueError):
            MODULE.rollback_plan(plan)

    def test_rollback_rejects_unknown_configuration(self):
        plan = self.plan()
        plan["resource_changes"][0]["change"]["after_unknown"]["template"] = True
        with self.assertRaises(ValueError):
            MODULE.rollback_plan(plan)

    def test_create_delete_replace_and_unrelated_resource_updates_are_rejected(self):
        for actions in [["create"], ["delete"], ["create", "delete"]]:
            plan = self.plan()
            plan["resource_changes"][0]["change"]["actions"] = actions
            with self.assertRaises(ValueError):
                MODULE.rollback_plan(plan)
        plan = self.plan()
        plan["resource_changes"][0]["type"] = "azurerm_key_vault"
        with self.assertRaises(ValueError):
            MODULE.rollback_plan(plan)

    def test_noop_rollback_is_safe(self):
        self.assertEqual(MODULE.rollback_plan({"resource_changes": []}), 0)

    def test_invalid_run_identifier_is_rejected_before_command(self):
        with patch.object(MODULE, "run") as run:
            for value in ["../other", "-1", "0", "123;echo bad"]:
                with self.assertRaises(ValueError):
                    MODULE.workflow_run(value)
            run.assert_not_called()

    def test_prepare_pins_historical_images_and_preserves_current_configuration(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "ROLLBACK_DEPLOYMENT_RUN_ID": "123",
                    "GITHUB_SHA": "c" * 40,
                    "AZURE_SUBSCRIPTION_ID": "sub",
                    "AZURE_TENANT_ID": "tenant",
                    "APPLICATION_TFVARS_JSON": json.dumps(
                        {
                            "subscription_id": "sub",
                            "tenant_id": "tenant",
                            "max_output_tokens": 512,
                        }
                    ),
                    "TF_BACKEND_JSON": json.dumps({"use_azuread_auth": True}),
                },
            ),
            patch.object(MODULE, "workflow_run", return_value=self.metadata()),
        ):
            directory = Path(temp)
            record = directory / "deployment.json"
            record.write_text(json.dumps(self.record()))
            MODULE.prepare(directory / "plan", record)
            variables = json.loads(
                (directory / "plan/application.tfvars.json").read_text()
            )
            self.assertEqual(variables["max_output_tokens"], 512)
            self.assertIn("@sha256:", variables["container_image"])
            self.assertEqual(
                json.loads((directory / "plan/release.json").read_text())["source_sha"],
                "a" * 40,
            )


class DiagnosticTests(unittest.TestCase):
    def test_failure_logs_are_bounded_and_only_ciphertext_is_preserved(self):
        encryption_key = b"x" * 32
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "PLAN_ENCRYPTION_KEY": base64.b64encode(encryption_key).decode(),
                },
            ),
        ):
            directory = Path(temp)
            (directory / "plan.log").write_text("X" * 300000 + "PRIVATE_DIAGNOSTIC")
            (directory / "application.tfplan").write_text("DO_NOT_INCLUDE_PLAN")
            MODULE.diagnostics(directory)
            cipher = (directory / "diagnostics.enc").read_bytes()
            self.assertNotIn(b"PRIVATE_DIAGNOSTIC", cipher)
            payload = json.loads(
                AESGCM(encryption_key).decrypt(
                    cipher[:12], cipher[12:], b"odyn-delivery-diagnostics-v1"
                )
            )
            self.assertEqual(set(payload["logs"]), {"plan.log"})
            self.assertLessEqual(len(payload["logs"]["plan.log"]), 262144)
            self.assertTrue(payload["logs"]["plan.log"].endswith("PRIVATE_DIAGNOSTIC"))
            self.assertEqual(
                (directory / "diagnostics.enc").stat().st_mode & 0o777, 0o600
            )

    def test_symlink_log_is_not_archived(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "PLAN_ENCRYPTION_KEY": base64.b64encode(b"x" * 32).decode(),
                },
            ),
        ):
            directory = Path(temp)
            (directory / "private").write_text("DO_NOT_READ")
            (directory / "plan.log").symlink_to(directory / "private")
            MODULE.diagnostics(directory)
            cipher = (directory / "diagnostics.enc").read_bytes()
            data = AESGCM(b"x" * 32).decrypt(
                cipher[:12], cipher[12:], b"odyn-delivery-diagnostics-v1"
            )
            self.assertNotIn(b"DO_NOT_READ", data)

    def test_failed_subprocess_saves_private_diagnostic_without_printing(self):
        import subprocess

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"ODYN_DELIVERY_DIR": temp}),
            patch.object(
                MODULE.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(
                    1, ["terraform"], stderr="PRIVATE_ERROR"
                ),
            ),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                MODULE.run(["terraform"])
            path = Path(temp) / "delivery-error.log"
            self.assertEqual(path.read_text(), "PRIVATE_ERROR")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_helper_exception_is_saved_for_encrypted_diagnostics(self):
        encryption_key = b"y" * 32
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "ODYN_DELIVERY_DIR": temp,
                    "PLAN_ENCRYPTION_KEY": base64.b64encode(encryption_key).decode(),
                },
            ),
        ):
            MODULE.save_failure(ValueError("adoption precondition failed"))
            path = Path(temp) / "delivery-error.log"
            self.assertIn("ValueError: adoption precondition failed", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            MODULE.diagnostics(Path(temp))
            cipher = (Path(temp) / "diagnostics.enc").read_bytes()
            self.assertNotIn(b"adoption precondition failed", cipher)
            payload = AESGCM(encryption_key).decrypt(
                cipher[:12], cipher[12:], b"odyn-delivery-diagnostics-v1"
            )
            self.assertIn(b"adoption precondition failed", payload)

    def test_failure_category_reports_only_safe_allowlisted_codes(self):
        cases = (
            ("AuthorizationFailed: PRIVATE_RESOURCE_ID", "AZURE_AUTHORIZATION"),
            ("QuotaExceeded: PRIVATE_SUBSCRIPTION", "AZURE_QUOTA"),
            ("RequestDisallowedByPolicy", "AZURE_POLICY"),
            ("Error acquiring the state lock", "TERRAFORM_STATE_LOCK"),
            ("arbitrary private diagnostic", "TERRAFORM_PLAN_FAILED"),
            ("", "TERRAFORM_FAILURE_UNCLASSIFIED"),
        )
        for diagnostic, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                if diagnostic:
                    (directory / "plan.log").write_text(diagnostic)
                with patch("builtins.print") as output:
                    actual = MODULE.failure_category(directory)
                self.assertEqual(actual, expected)
                rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
                self.assertIn(expected, rendered)
                self.assertNotIn("PRIVATE_", rendered)

    def test_failure_category_classifies_init_and_adoption_errors(self):
        cases = (
            (
                "init.log",
                "AuthorizationFailed: PRIVATE_RESOURCE_ID",
                "AZURE_AUTHORIZATION",
            ),
            (
                "delivery-error.log",
                "Error acquiring the state lock",
                "TERRAFORM_STATE_LOCK",
            ),
            (
                "delivery-error.log",
                "private adoption detail",
                "DELIVERY_HELPER_FAILED",
            ),
        )
        for filename, diagnostic, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / filename).write_text(diagnostic)
                with patch("builtins.print") as output:
                    actual = MODULE.failure_category(directory)
                self.assertEqual(actual, expected)
                rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
                self.assertIn(expected, rendered)
                self.assertNotIn("PRIVATE_", rendered)

    def test_foundation_workflow_encrypts_failure_logs_before_cleanup(self):
        workflow = (ROOT / ".github/workflows/infrastructure.yml").read_text()
        self.assertEqual(
            workflow.count("Encrypt foundation plan diagnostics on failure"), 1
        )
        self.assertEqual(
            workflow.count("Encrypt foundation apply diagnostics on failure"), 1
        )
        self.assertEqual(
            workflow.count("encrypted-foundation-diagnostics-${{ github.job }}"), 2
        )
        self.assertLess(
            workflow.index("Encrypt foundation plan diagnostics on failure"),
            workflow.index("Remove private temporary files"),
        )
