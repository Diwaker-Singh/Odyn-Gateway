"""Parse repository configuration and enforce deployment/CI contracts offline."""

import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import tomllib
import yaml
from jsonschema import Draft4Validator

ROOT = Path(__file__).resolve().parent.parent


def main():
    parsed = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(
            part.startswith(".venv")
            or part
            in (
                ".git",
                ".secrets",
                ".terraform",
                "__pycache__",
                "build",
                "dist",
            )
            for part in path.parts
        ):
            continue
        suffix = path.suffix
        if suffix == ".py":
            ast.parse(path.read_text(), filename=str(path.relative_to(ROOT)))
        elif suffix == ".toml":
            tomllib.loads(path.read_text())
        elif suffix in (".yaml", ".yml"):
            yaml.safe_load(path.read_text())
        elif suffix == ".json":
            json.loads(path.read_text())
        elif path.name.endswith(".xml.tftpl"):
            tree = ET.fromstring(re.sub(r"\$\{[^}]+\}", "fixture", path.read_text()))
            assert tree.tag == "policies"
            assert tree.find("backend/forward-request") is not None
            assert tree.find("inbound/forward-request") is None
            assert tree.find("inbound/validate-jwt") is not None
        else:
            continue
        parsed += 1
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert workflow.get("permissions") == {"contents": "read"}
    plugin_init = next(
        step
        for step in workflow["jobs"]["tflint"]["steps"]
        if "--init" in step.get("run", "")
    )
    assert plugin_init["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            command = step.get("run", "")
            if command.strip() == "bash scripts/deploy.sh preflight":
                continue  # Explicit local-only wrapper; never permits plan/apply.
            assert not re.search(
                r"\b(terraform.*\b(?:plan|apply|destroy)|az\s+(?:login|keyvault)|set-provider-key|scripts/deploy)\b",
                command,
            )
    for filename, initial_job, protected_job, environment in (
        ("release.yml", "build", "publish", "azure-release"),
        ("deploy.yml", "plan", "apply", "azure-deploy"),
        ("infrastructure.yml", "plan", "apply", "azure-foundation-apply"),
    ):
        delivery = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        triggers = delivery.get("on", delivery.get(True))
        assert list(triggers) == (
            ["workflow_dispatch", "workflow_call"]
            if filename == "deploy.yml"
            else ["workflow_dispatch"]
        )
        assert delivery["permissions"] == {"contents": "read"}
        assert delivery["concurrency"]["cancel-in-progress"] is False
        jobs = delivery["jobs"]
        assert "refs/heads/main" in jobs[initial_job]["if"]
        feature_flag = (
            "AZURE_FOUNDATION_ENABLED"
            if filename == "infrastructure.yml"
            else "AZURE_DELIVERY_ENABLED"
        )
        assert feature_flag in jobs[initial_job]["if"]
        assert "!inputs.validation_only" in jobs[initial_job]["if"]
        assert jobs[initial_job]["needs"] == "validate"
        assert jobs["validate"]["uses"] == "./.github/workflows/validate-delivery.yml"
        assert (
            triggers["workflow_dispatch"]["inputs"]["validation_only"]["default"]
            is True
        )
        assert jobs[protected_job]["needs"] == initial_job
        expected_environment = (
            "${{ inputs.target == 'disposable' && '"
            + environment.replace("azure-", "azure-test-", 1)
            + "' || '"
            + environment
            + "' }}"
        )
        assert jobs[protected_job]["environment"] == expected_environment
        profile = triggers["workflow_dispatch"]["inputs"]["target"]
        assert profile["options"] == ["deployment", "disposable"]
        assert profile["default"] == "deployment"
        assert jobs[protected_job]["permissions"]["id-token"] == "write"
        for job in jobs.values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/upload-artifact"):
                    assert ".tfplan" not in step["with"]["path"]
                    assert ".tfvars" not in step["with"]["path"]
    rollback = yaml.safe_load((ROOT / ".github/workflows/rollback.yml").read_text())
    triggers = rollback.get("on", rollback.get(True))
    assert list(triggers) == ["workflow_dispatch"]
    assert triggers["workflow_dispatch"]["inputs"]["validation_only"]["default"] is True
    job = rollback["jobs"]["rollback"]
    assert job["uses"] == "./.github/workflows/deploy.yml"
    assert (
        job["with"]["rollback_deployment_run_id"] == "${{ inputs.deployment_run_id }}"
    )
    assert job["permissions"] == {
        "contents": "read",
        "actions": "read",
        "id-token": "write",
    }
    deployment = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    scans = [
        step
        for step in deployment["jobs"]["plan"]["steps"]
        if step.get("name", "").startswith("Rescan prior")
    ]
    assert len(scans) == 2
    for step in scans:
        assert step["if"] == "${{ inputs.rollback_deployment_run_id != '' }}"
        assert step["env"]["TRIVY_IMAGE_SRC"] == "remote"
        assert step["with"]["severity"] == "HIGH,CRITICAL"
        assert step["with"]["exit-code"] == "1"
        assert step["with"]["image-ref"].startswith("${{ steps.prepared.outputs.")
    recovery = yaml.safe_load(
        (ROOT / ".github/workflows/state-recovery.yml").read_text()
    )
    recovery_triggers = recovery.get("on", recovery.get(True))
    assert list(recovery_triggers) == ["workflow_dispatch"]
    assert (
        recovery_triggers["workflow_dispatch"]["inputs"]["validation_only"]["default"]
        is True
    )
    assert recovery["permissions"] == {"contents": "read"}
    assert recovery["jobs"]["archive"]["environment"] == "azure-recovery-archive"
    assert "STATE_RECOVERY_ENABLED" in recovery["jobs"]["restore"]["if"]
    assert recovery["jobs"]["restore"]["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "odyn-azure",
    ]
    assert any(
        step.get("name") == "Require passing CI for this source"
        for step in recovery["jobs"]["restore"]["steps"]
    )
    assert not any(
        step.get("uses", "").startswith("azure/login")
        for step in recovery["jobs"]["archive"]["steps"]
    )
    for step in recovery["jobs"]["archive"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact"):
            assert step["with"]["path"].endswith("/recovery.enc")
    operations = yaml.safe_load((ROOT / ".github/workflows/operations.yml").read_text())
    triggers = operations.get("on", operations.get(True))
    assert list(triggers) == ["workflow_dispatch"]
    assert triggers["workflow_dispatch"]["inputs"]["validation_only"]["default"] is True
    assert operations["permissions"] == {"contents": "read"}
    inventory = operations["jobs"]["inventory"]
    assert inventory["environment"] == (
        "${{ inputs.target == 'disposable' && 'azure-test-operations' "
        "|| 'azure-operations' }}"
    )
    assert "AZURE_OPERATIONS_ENABLED" in inventory["if"]
    assert "!inputs.validation_only" in inventory["if"]
    commands = "\n".join(step.get("run", "") for step in inventory["steps"])
    assert "scripts/teardown.py" in commands and "--execute" not in commands
    helper = yaml.safe_load(
        (ROOT / ".github/workflows/validate-delivery.yml").read_text()
    )
    assert helper["permissions"] == {"contents": "read"}
    assert all(
        "azure/login" not in step.get("uses", "")
        for step in helper["jobs"]["helpers"]["steps"]
    )
    schema = json.loads(
        (ROOT / "infra/modules/llm-gateway/azure/chat-request.schema.json").read_text()
    )
    Draft4Validator.check_schema(schema)
    validator = Draft4Validator(schema)
    cases = json.loads(
        (ROOT / "app/tests/fixtures/chat-structure-cases.json").read_text()
    )
    for case in cases:
        assert validator.is_valid(case["payload"]) == case["edge_valid"], case["name"]
    for lock in (
        "requirements.lock",
        "requirements-runtime.lock",
        "requirements-scan.lock",
    ):
        content = (ROOT / "app" / lock).read_text()
        assert "--hash=sha256:" in content
        assert not re.search(r"^\w[^\n]*>=", content, re.MULTILINE)
    print(
        f"Parsed {parsed} configurations/sources; policy XML, CI and lock contracts passed."
    )


if __name__ == "__main__":
    main()
