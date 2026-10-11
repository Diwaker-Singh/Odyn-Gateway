"""Scan the Git source tree and history, excluding ignored local credentials."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def candidate_paths(root):
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted({Path(p.decode()) for p in result.stdout.split(b"\0") if p})


def safe_candidate(path):
    return not (
        path.is_absolute()
        or ".." in path.parts
        or any(
            part
            in {
                ".secrets",
                ".venv",
                ".venv-scan",
                ".terraform",
                "logs",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "node_modules",
                "dist",
                "build",
            }
            for part in path.parts
        )
        or (path.name.startswith(".env") and path.name != ".env.example")
        or (path.name.endswith(".tfvars") and not path.name.endswith(".example.tfvars"))
        or ".tfstate" in path.name
        or path.name.endswith(
            (".tfplan", ".key", ".pem", ".pfx", ".p12", ".log", ".pyc", ".pyo")
        )
    )


def main():
    paths = candidate_paths(ROOT)
    if not paths:
        print("FAIL: empty source tree")
        return 2
    if any(not safe_candidate(path) for path in paths):
        print("FAIL: forbidden private/build file in Git source; no contents printed")
        return 2
    with tempfile.TemporaryDirectory(prefix="odyn-candidate-") as directory:
        destination = Path(directory)
        for path in paths:
            source = ROOT / path
            if source.is_symlink():
                print("FAIL: symlink requires explicit repository review")
                return 2
            if not source.is_file():
                continue  # Git may still list a tracked deletion.
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--source",
                str(destination),
                "--no-git",
                "--redact",
                "--report-format",
                "json",
                "--report-path",
                str(destination / ".candidate-report.json"),
                "--config",
                str(ROOT / ".gitleaks.toml"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        print(
            "Candidate secret scan:",
            "passed" if result.returncode == 0 else "failed",
            "(" + str(len(paths)) + " files)",
        )
        if result.returncode:
            report = destination / ".candidate-report.json"
            if report.exists():
                for finding in json.loads(report.read_text()):
                    file = str(finding.get("File", ""))
                    file = file.removeprefix(str(destination) + "/")
                    print(
                        "Finding:",
                        finding.get("RuleID"),
                        file,
                        finding.get("StartLine"),
                    )
            return result.returncode
    history = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if history.returncode == 0:
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--source",
                str(ROOT),
                "--redact",
                "--config",
                str(ROOT / ".gitleaks.toml"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        print("History secret scan:", "passed" if result.returncode == 0 else "failed")
        return result.returncode
    print("No commit history exists; candidate scan is local tree evidence only.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(
            "Candidate scan blocked: required tool/input unavailable."
        ) from None
