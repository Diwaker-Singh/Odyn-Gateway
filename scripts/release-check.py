"""Local release gate; no installs, init, login, daemon start or cloud actions."""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from source_fingerprint import source_digest

ROOT = Path(__file__).resolve().parent.parent


def check_scope(name: str) -> str:
    """Describe where a release check actually ran and what it inspected."""
    if name in {"image_platform", "image", "collector_image"}:
        return "local_container"
    if name in {"tests", "package"}:
        return "local_verified"
    return "static_only"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--collector-image", default="odyn-collector:azure")
    parser.add_argument("--include-image", action="store_true")
    args = parser.parse_args()
    directory = Path(tempfile.gettempdir()) / "odyn-gateway-release-validation"
    directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": "app/src:app/tests", "PY": sys.executable}
    tf = shutil.which("terraform") or str(Path.home() / ".local/bin/terraform")
    lint = shutil.which("tflint") or str(Path.home() / ".local/bin/tflint")
    scanner = ROOT / ".venv-scan/bin/python"
    checks = [
        (
            "compile",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "app/src",
                "app/tests",
                "scripts",
            ],
        ),
        (
            "tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "app/tests", "-v"],
        ),
        ("ruff", [sys.executable, "-m", "ruff", "check", "app", "scripts"]),
        (
            "format",
            [sys.executable, "-m", "ruff", "format", "--check", "app", "scripts"],
        ),
        (
            "types",
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                "app/pyproject.toml",
                "app/src",
            ],
        ),
        ("dependencies", [sys.executable, "-m", "pip", "check"]),
        ("policy", [sys.executable, "scripts/policy-guard.py"]),
        ("configs", [str(scanner), "scripts/check-configs.py"]),
        ("shell", ["bash", "-n", "scripts/deploy.sh", "scripts/set-provider-key.sh"]),
        ("package", [sys.executable, "-m", "build", "--no-isolation", "app"]),
        ("terraform_fmt", [tf, "fmt", "-check", "-recursive", "infra"]),
        ("terraform_module", [tf, "-chdir=infra/modules/llm-gateway", "validate"]),
        ("terraform_environment", [tf, "-chdir=infra/envs/dev/azure", "validate"]),
        ("terraform_bootstrap", [tf, "-chdir=infra/bootstrap", "validate"]),
        ("terraform_artifacts", [tf, "-chdir=infra/artifacts", "validate"]),
        ("tflint", [lint, "--chdir=infra/modules/llm-gateway"]),
        (
            "checkov",
            [
                str(scanner),
                "-m",
                "checkov.main",
                "-d",
                "infra",
                "--framework",
                "terraform",
                "--skip-download",
                "--quiet",
                "--compact",
            ],
        ),
        ("gitleaks", [sys.executable, "scripts/scan_repo.py"]),
    ]
    if args.include_image:
        checks.append(
            (
                "image_platform",
                [
                    sys.executable,
                    "scripts/image-check.py",
                    "--gateway",
                    args.image or "odyn-gateway:azure",
                    "--collector",
                    args.collector_image,
                ],
            )
        )
        checks.append(
            (
                "image",
                [
                    "trivy",
                    "image",
                    "--exit-code",
                    "1",
                    "--severity",
                    "HIGH,CRITICAL",
                    args.image or "odyn-gateway:azure",
                ],
            )
        )
    if args.include_image:
        checks.append(
            (
                "collector_image",
                [
                    "trivy",
                    "image",
                    "--exit-code",
                    "1",
                    "--severity",
                    "HIGH,CRITICAL",
                    args.collector_image,
                ],
            )
        )
    results = []
    for name, command in checks:
        status, code, output = "blocked", None, "tool unavailable"
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
            code = result.returncode
            output = result.stdout + result.stderr
            status = "passed" if code == 0 else "failed"
            if name == "tests" and not re.search(r"Ran [1-9][0-9]* tests", output):
                status = "failed"
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            output, status = "check timeout", "failed"
        output = re.sub(
            r"(?i)(Bearer\s+|AccountKey=)[A-Za-z0-9+/=._-]+", r"\1[REDACTED]", output
        )
        output = output.replace(str(ROOT), "<project>").replace(
            str(Path.home()), "<home>"
        )
        # Tool output commonly ends with multiple newlines or terminal padding;
        # keep generated evidence clean for diffs and reviewer copy/paste.
        output = output.rstrip() + "\n"
        artifact = directory / f"{name}.txt"
        artifact.write_text(output)
        results.append(
            {
                "id": name,
                "status": status,
                "scope": check_scope(name),
                "command": [
                    part.replace(str(ROOT), ".").replace(str(Path.home()), "~")
                    for part in command
                ],
                "exit_code": code,
                "report": "local command result; no generated output is tracked",
            }
        )
        print(name, status)
    summary = {
        state: sum(item["status"] == state for item in results)
        for state in ("passed", "failed", "blocked")
    }
    report = {
        "schema_version": 1,
        "source_sha256": source_digest(ROOT),
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cloud_verified": False,
        "image_included": args.include_image,
        "checks": results,
        "summary": summary,
    }
    report_dir = ROOT / ".artifacts"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "release-results.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return 1 if summary["failed"] or summary["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
