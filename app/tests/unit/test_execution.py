"""Private execution bootstrap boundaries; no Azure or GitHub calls."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "execution", ROOT / "scripts/execution.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ExecutionTests(unittest.TestCase):
    def config(self):
        return {
            "group": "rg-odyn-gateway-execution-test19",
            "location": "centralindia",
            "network": "10.47.0.0/24",
            "minutes": 30,
            "groups": [],
        }

    def environment(self):
        return {
            "EXECUTION_CONFIG_JSON": json.dumps(self.config()),
            "AZURE_SUBSCRIPTION_ID": "11111111-1111-4111-8111-111111111111",
            "AZURE_TENANT_ID": "22222222-2222-4222-8222-222222222222",
            "AZURE_CLIENT_ID": "33333333-3333-4333-8333-333333333333",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/heads/main",
        }

    def test_configuration_validates_before_writes(self):
        with patch.dict(os.environ, self.environment()):
            self.assertEqual(M.config(), self.config())
        for field, value in (
            ("group", "rg-production"),
            ("network", "8.8.8.0/24"),
            ("minutes", 999),
            ("network", "10.0.0.0/8"),
        ):
            config = self.config() | {field: value}
            with (
                patch.dict(
                    os.environ,
                    self.environment() | {"EXECUTION_CONFIG_JSON": json.dumps(config)},
                ),
                self.assertRaises(ValueError),
            ):
                M.config()

    def test_refuses_owner_and_cross_subscription_network(self):
        cases = [
            self.config()
            | {
                "groups": [
                    {
                        "name": "rg-odyn-gateway-test19",
                        "grants": [
                            {
                                "principal": "11111111-1111-4111-8111-111111111111",
                                "role": "Owner",
                            }
                        ],
                    }
                ]
            },
            self.config()
            | {
                "application_vnet": (
                    "/subscriptions/other/resourceGroups/app/"
                    "providers/Microsoft.Network/virtualNetworks/app"
                )
            },
        ]
        for config in cases:
            with (
                patch.dict(
                    os.environ,
                    self.environment() | {"EXECUTION_CONFIG_JSON": json.dumps(config)},
                ),
                self.assertRaises(ValueError),
            ):
                M.config()

    def test_wrong_identity_stops_before_writes(self):
        with (
            patch.dict(os.environ, self.environment()),
            patch.object(M, "az", return_value={}) as az,
            self.assertRaises(ValueError),
        ):
            M.identity()
        self.assertEqual(az.call_count, 1)

    def test_unowned_execution_group_cannot_be_deleted(self):
        with (
            patch.object(
                M, "az", side_effect=[True, {"tags": {"managed_by": "other"}}]
            ) as az,
            self.assertRaises(ValueError),
        ):
            M.stop(self.config())
        self.assertFalse(any("delete" in call.args for call in az.call_args_list))

    def test_token_is_protected_and_file_is_private(self):
        def inspect(*args):
            path = Path(args[-1][1:])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            body = json.loads(path.read_text())
            self.assertNotIn("test-secret", body["properties"]["source"]["script"])
            self.assertEqual(
                body["properties"]["protectedParameters"][0]["value"], "test-secret"
            )
            self.assertNotIn(
                "test-secret", json.dumps(body["properties"]["parameters"])
            )

        with (
            patch.dict(os.environ, self.environment()),
            patch.object(M, "az", side_effect=inspect),
        ):
            M.protected_start(self.config(), "test-secret", 123)

    def test_install_contains_no_registration_token(self):
        source = (ROOT / "scripts/execution-install.sh").read_text()
        self.assertNotIn("REGISTRATION_TOKEN", source)
        self.assertIn("sha256sum -c", source)
        self.assertNotIn("--token", source)

    def test_runner_has_explicit_clean_user_environment(self):
        source = (ROOT / "scripts/execution-start.sh").read_text()
        self.assertIn("env -i", source)
        self.assertIn("HOME=/home/odynrunner", source)
        self.assertNotIn("--preserve-environment", source)

    def test_restore_groups_is_scoped_and_deterministic(self):
        value = self.config() | {
            "groups": [
                {
                    "name": "rg-odyn-gateway-test19",
                    "grants": [
                        {
                            "principal": "11111111-1111-4111-8111-111111111111",
                            "role": "Reader",
                        }
                    ],
                }
            ]
        }
        with (
            patch.dict(os.environ, self.environment()),
            patch.object(M, "az", side_effect=[False, {}, [], {}]) as az,
        ):
            M.restore_groups(value)
        grant = az.call_args.args
        self.assertEqual(
            grant[-1],
            "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-odyn-gateway-test19",
        )
        self.assertIn("--assignee-object-id", grant)

    def test_workflow_is_main_only_protected_and_can_run_without_private_runner(self):
        source = (ROOT / ".github/workflows/execution.yml").read_text()
        for expected in (
            "workflow_dispatch:",
            "default: true",
            "azure-execution",
            "runs-on: ubuntu-latest",
            "refs/heads/main",
            "AZURE_EXECUTION_ENABLED",
            "failure()",
            "python scripts/execution.py stop",
        ):
            self.assertIn(expected, source)

    def test_token_only_retirement_waits_for_managed_command(self):
        responses = [
            True,
            {"tags": {"managed_by": M.OWNER}},
            [{"name": "runner"}],
            {"instanceView": {"executionState": "Succeeded"}},
            None,
            False,
        ]
        with (
            patch.dict(
                os.environ,
                self.environment()
                | {"GH_TOKEN": "", "RUNNER_REMOVAL_TOKEN": "short-lived"},
            ),
            patch.object(M, "az", side_effect=responses) as az,
            patch.object(M, "protected_start") as protected,
            patch.object(M, "network_links"),
        ):
            M.stop(self.config())
        protected.assert_called_once_with(
            self.config(), "short-lived", 0, stopping=True
        )
        status_call = az.call_args_list[3].args
        self.assertIn("--run-command-name", status_call)
        self.assertIn("retire", status_call)

    def test_partial_cleanup_skips_absent_external_storage_group(self):
        value = self.config() | {
            "storage": [
                {
                    "id": (
                        "/subscriptions/sub/resourceGroups/state/"
                        "providers/Microsoft.Storage/storageAccounts/state"
                    )
                }
            ]
        }
        with (
            patch.dict(os.environ, self.environment()),
            patch.object(M, "az", return_value=False) as az,
        ):
            M.network_links(value, remove=True)
        self.assertEqual(az.call_count, 1)

    def test_running_execution_cannot_be_adopted(self):
        with (
            patch.object(
                M,
                "az",
                side_effect=[
                    True,
                    {"tags": {"managed_by": M.OWNER}},
                    [{"name": "existing"}],
                ],
            ) as az,
            self.assertRaises(ValueError),
        ):
            M.start(self.config())
        self.assertFalse(any("create" in call.args for call in az.call_args_list))

    def test_failed_start_cannot_delete_another_runs_lease(self):
        responses = [
            True,
            {"tags": {"managed_by": M.OWNER}},
            {"tags": {"execution_run": "earlier"}},
        ]
        with (
            patch.object(M, "az", side_effect=responses) as az,
            self.assertRaises(ValueError),
        ):
            M.stop(self.config(), only_run="new")
        self.assertFalse(any("delete" in call.args for call in az.call_args_list))

    def test_retained_scope_removes_resources_without_deleting_iam_group(self):
        def command(*args):
            if args[:3] == ("deployment", "group", "create"):
                path = Path(args[-1])
                self.assertEqual(json.loads(path.read_text())["resources"], [])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertIn("Complete", args)
                return {}
            return []

        with patch.object(M, "az", side_effect=command) as az:
            M.retire_resources(self.config())
        self.assertFalse(
            any(call.args[:2] == ("group", "delete") for call in az.call_args_list)
        )

    def test_unexpected_execution_resource_blocks_complete_mode(self):
        with (
            patch.object(
                M,
                "az",
                return_value=[
                    {"name": "manual", "type": "Microsoft.Storage/storageAccounts"}
                ],
            ) as az,
            self.assertRaises(ValueError),
        ):
            M.retire_resources(self.config())
        self.assertEqual(az.call_count, 1)

    def test_azure_generated_disk_name_is_retired(self):
        resources = [
            {
                "name": "runner_disk1_47de39a66dcc43a68bc47bfee3731547",
                "type": "Microsoft.Compute/disks",
            }
        ]
        with patch.object(M, "az", side_effect=[resources, {}, []]) as az:
            M.retire_resources(self.config())
        self.assertEqual(
            az.call_args_list[1].args[:3], ("deployment", "group", "create")
        )

    def test_retained_resource_check_must_be_empty_after_retirement(self):
        resources = [{"name": "runner", "type": "Microsoft.Compute/virtualMachines"}]
        with (
            patch.object(M, "az", side_effect=[resources, {}, resources]),
            self.assertRaises(RuntimeError),
        ):
            M.retire_resources(self.config())


class GroupAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "delivery_adoption", ROOT / "scripts/delivery.py"
        )
        cls.delivery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.delivery)

    def test_import_only_owned_empty_group(self):
        module = self.delivery
        for tags, resources, expected in (
            ({"managed_by": "odyn-execution-pipeline"}, [], True),
            ({"managed_by": "terraform"}, [], False),
            (
                {
                    "project": "odyn-gateway",
                    "purpose": "terraform-state",
                    "managed_by": "terraform",
                },
                [],
                True,
            ),
            ({"managed_by": "odyn-execution-pipeline"}, [{"id": "survivor"}], False),
            (
                {
                    "project": "odyn-gateway",
                    "purpose": "terraform-state",
                    "managed_by": "terraform",
                },
                [{"id": "survivor"}],
                False,
            ),
        ):
            responses = [
                '{"exists": false}',
                "true",
                json.dumps({"id": "/destination", "tags": tags}),
            ]
            if expected or resources:
                responses.append(json.dumps(resources))
            if expected:
                responses.append("")
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(
                    os.environ,
                    {
                        "AZURE_RESOURCE_GROUP": "rg-odyn-gateway-test19",
                        "ODYN_INFRA_COMPONENT": "bootstrap",
                        "TF_BACKEND_JSON": json.dumps(
                            {
                                "storage_account_name": "state",
                                "container_name": "state",
                                "key": "new.tfstate",
                            }
                        ),
                    },
                ),
                patch.object(module, "context"),
                patch.object(module, "run", side_effect=responses) as run,
            ):
                if expected:
                    module.adopt_bootstrap_group(Path(temporary))
                    self.assertIn("import", run.call_args.args[0])
                else:
                    with self.assertRaises(ValueError):
                        module.adopt_bootstrap_group(Path(temporary))
                    self.assertFalse(
                        any("import" in call.args[0] for call in run.call_args_list)
                    )

    def test_existing_state_address_is_never_imported_again(self):
        with (
            patch.dict(
                os.environ,
                {
                    "ODYN_INFRA_COMPONENT": "bootstrap",
                    "TF_BACKEND_JSON": json.dumps(
                        {
                            "storage_account_name": "state",
                            "container_name": "state",
                            "key": "old.tfstate",
                        }
                    ),
                },
            ),
            patch.object(self.delivery, "context"),
            patch.object(
                self.delivery,
                "run",
                side_effect=['{"exists": true}', "azurerm_resource_group.state"],
            ) as run,
        ):
            self.delivery.adopt_bootstrap_group(Path("/tmp/private"))
        self.assertEqual(run.call_count, 2)

    def test_backend_unknown_is_not_treated_as_empty(self):
        env = {
            "ODYN_INFRA_COMPONENT": "bootstrap",
            "TF_BACKEND_JSON": json.dumps(
                {
                    "storage_account_name": "state",
                    "container_name": "state",
                    "key": "old.tfstate",
                }
            ),
        }
        with (
            patch.dict(os.environ, env),
            patch.object(self.delivery, "context"),
            patch.object(self.delivery, "run", return_value='{"exists": null}') as run,
            self.assertRaises(TypeError),
        ):
            self.delivery.adopt_bootstrap_group(Path("/tmp/private"))
        self.assertEqual(run.call_count, 1)


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "lifecycle", ROOT / "scripts/lifecycle.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.real_preflight = staticmethod(cls.module.preflight)

    def setUp(self):
        preflight = patch.object(self.module, "preflight")
        self.preflight = preflight.start()
        self.addCleanup(preflight.stop)

    def test_orders_dependencies_and_pins_deploy_to_publication(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}),
            patch.object(self.module, "dispatch", return_value=42) as dispatch,
        ):
            self.module.recreate(30)
        calls = dispatch.call_args_list
        self.assertEqual(
            [c.args[0] for c in calls],
            [
                "execution.yml",
                "management.yml",
                "infrastructure.yml",
                "infrastructure.yml",
                "release.yml",
                "deploy.yml",
                "operations.yml",
                "execution.yml",
            ],
        )
        self.assertEqual(calls[5].args[1]["release_run_id"], "42")
        self.assertEqual(calls[-1].args[1], {"action": "stop"})

    def test_failed_stage_still_dispatches_retirement(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}),
            patch.object(
                self.module, "dispatch", side_effect=[1, RuntimeError("failure"), 2]
            ) as dispatch,
            self.assertRaises(RuntimeError),
        ):
            self.module.recreate(30)
        self.assertEqual(
            dispatch.call_args.args[:2], ("execution.yml", {"action": "stop"})
        )

    def test_disposable_profile_is_forwarded_to_every_delivery_component(self):
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_REF": "refs/heads/main",
                    "LIFECYCLE_TARGET": "disposable",
                    "DISPOSABLE_CLEANUP_CONFIRMATION": "delete "
                    + "a" * 36
                    + " rg-odyn-gateway-auto19",
                },
            ),
            patch.object(self.module, "dispatch", return_value=42) as dispatch,
        ):
            self.module.recreate(30)
        for call in dispatch.call_args_list:
            if call.args[0] != "execution.yml":
                self.assertEqual(call.args[1]["target"], "disposable")
        self.assertEqual(dispatch.call_args_list[-2].args[0], "cleanup.yml")

    def test_unknown_profile_cannot_allocate_execution(self):
        with (
            patch.dict(
                os.environ,
                {"GITHUB_REF": "refs/heads/main", "LIFECYCLE_TARGET": "unexpected"},
            ),
            patch.object(self.module, "dispatch") as dispatch,
            self.assertRaises(ValueError),
        ):
            self.module.recreate(30)
        dispatch.assert_not_called()

    def test_disposable_profile_requires_cleanup_confirmation_before_allocation(self):
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_REF": "refs/heads/main",
                    "LIFECYCLE_TARGET": "disposable",
                    "DISPOSABLE_CLEANUP_CONFIRMATION": "",
                },
            ),
            patch.object(self.module, "dispatch") as dispatch,
            self.assertRaises(ValueError),
        ):
            self.module.recreate(30)
        dispatch.assert_not_called()

    def test_source_move_blocks_setup_before_dispatch(self):
        env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "old",
            "GITHUB_RUN_ID": "1",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(self.module, "gh", return_value={"sha": "new"}) as gh,
            self.assertRaises(ValueError),
        ):
            self.module.dispatch("management.yml", {}, 999999999)
        self.assertEqual(gh.call_count, 1)

    def test_controller_requires_protected_main(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/feature"}),
            patch.object(self.module, "dispatch") as dispatch,
            self.assertRaises(ValueError),
        ):
            self.module.recreate(30)
        dispatch.assert_not_called()

    def test_dispatch_cli_url_is_not_parsed_as_json(self):
        result = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "https://github.com/owner/repo/actions/runs/1"},
        )()
        with patch.object(self.module.subprocess, "run", return_value=result):
            self.assertIsNone(self.module.gh("workflow", "run", "execution.yml"))

    def test_active_child_blocks_runner_destruction(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}),
            patch.object(
                self.module,
                "dispatch",
                side_effect=self.module.ActiveChildError("apply still active"),
            ) as dispatch,
            self.assertRaises(self.module.ActiveChildError),
        ):
            self.module.recreate(30)
        self.assertEqual(dispatch.call_count, 1)

    def test_failed_start_does_not_retire_existing_execution(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}),
            patch.object(
                self.module, "dispatch", side_effect=RuntimeError("start refused")
            ) as dispatch,
            self.assertRaises(RuntimeError),
        ):
            self.module.recreate(30)
        self.assertEqual(dispatch.call_count, 1)

    def test_disabled_component_blocks_setup_before_allocation(self):
        with (
            patch.dict(os.environ, {"LIFECYCLE_FLAGS_JSON": "{}"}),
            patch.object(self.module, "gh") as gh,
            self.assertRaises(ValueError),
        ):
            self.real_preflight()
        gh.assert_not_called()

    def test_disabled_cleanup_blocks_disposable_allocation(self):
        flags = {name: "true" for name in self.module.REQUIRED_FLAGS}
        with (
            patch.dict(
                os.environ,
                {
                    "LIFECYCLE_TARGET": "disposable",
                    "LIFECYCLE_FLAGS_JSON": json.dumps(flags),
                },
            ),
            patch.object(self.module, "gh") as gh,
            self.assertRaises(ValueError),
        ):
            self.real_preflight()
        gh.assert_not_called()

    def test_recovery_hydrates_state_between_backend_stages(self):
        environment = {
            "GITHUB_REF": "refs/heads/main",
            "LIFECYCLE_TARGET": "deployment",
            "LIFECYCLE_RECOVERY_RUN_ID": "123",
            "LIFECYCLE_RECOVERY_SHA256": "a" * 64,
            "AZURE_SUBSCRIPTION_ID": "b" * 36,
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(self.module, "dispatch", return_value=42) as dispatch,
        ):
            self.module.recreate(60)
        calls = dispatch.call_args_list
        names = [
            (c.args[0], c.args[1].get("action", c.args[1].get("component")))
            for c in calls
        ]
        self.assertEqual(
            names[2:6],
            [
                ("state-recovery.yml", "restore-foundation"),
                ("infrastructure.yml", "bootstrap"),
                ("state-recovery.yml", "restore-application"),
                ("infrastructure.yml", "registry"),
            ],
        )
        self.assertEqual(calls[2].args[1]["archive_run_id"], "123")
        self.assertEqual(
            calls[4].args[1]["confirmation"], "restore " + "b" * 36 + " application"
        )

    def test_recovery_not_allowed_into_disposable_profile(self):
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_REF": "refs/heads/main",
                    "LIFECYCLE_TARGET": "disposable",
                    "LIFECYCLE_RECOVERY_RUN_ID": "123",
                    "LIFECYCLE_RECOVERY_SHA256": "a" * 64,
                    "AZURE_SUBSCRIPTION_ID": "b" * 36,
                },
            ),
            patch.object(self.module, "dispatch") as dispatch,
            self.assertRaises(ValueError),
        ):
            self.module.recreate(60)
        dispatch.assert_not_called()

    def test_disabled_recovery_blocks_allocation(self):
        flags = {name: "true" for name in self.module.REQUIRED_FLAGS}
        with (
            patch.dict(
                os.environ,
                {
                    "LIFECYCLE_RECOVERY_RUN_ID": "123",
                    "LIFECYCLE_FLAGS_JSON": json.dumps(flags),
                },
            ),
            patch.object(self.module, "gh") as gh,
            self.assertRaises(ValueError),
        ):
            self.real_preflight()
        gh.assert_not_called()

    def test_validation_only_child_success_is_not_cloud_success(self):
        env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "source",
            "GITHUB_RUN_ID": "1",
        }
        responses = [
            {"sha": "source"},
            None,
            [
                {
                    "databaseId": 9,
                    "displayTitle": "management lifecycle-1-run",
                    "headSha": "source",
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
            {"jobs": [{"name": "provision", "conclusion": "skipped"}]},
        ]
        with (
            patch.dict(os.environ, env),
            patch.object(self.module, "gh", side_effect=responses),
            self.assertRaises(RuntimeError),
        ):
            self.module.dispatch("management.yml", {}, 999999999)

    def test_source_race_preserves_execution_instead_of_killing_unknown_child(self):
        env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "old",
            "GITHUB_RUN_ID": "1",
        }
        responses = [
            {"sha": "old"},
            None,
            [
                {
                    "databaseId": 9,
                    "displayTitle": "management lifecycle-1-run",
                    "headSha": "new",
                    "status": "in_progress",
                    "conclusion": "",
                }
            ],
        ]
        with (
            patch.dict(os.environ, env),
            patch.object(self.module, "gh", side_effect=responses),
            self.assertRaises(self.module.ActiveChildError),
        ):
            self.module.dispatch("management.yml", {}, 999999999)
