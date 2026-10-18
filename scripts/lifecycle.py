"""Run existing protected workflows in dependency order at one exact main revision.

The controller owns orchestration only. Azure privileges, saved-plan checks and
approvals remain in the component workflows. It never supplies provider secrets.
"""

import argparse
import json
import os
import subprocess
import time


def gh(*args):
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError("Lifecycle GitHub operation failed; inspect component runs")
    if args[0] != "api" and args[:2] not in (("run", "list"), ("run", "view")):
        return None  # Dispatch/cancel print human-readable output rather than JSON.
    return json.loads(result.stdout or "null")


def stages():
    return [
        ("execution.yml", {"action": "start"}),
        ("management.yml", {}),
        ("infrastructure.yml", {"component": "bootstrap"}),
        ("infrastructure.yml", {"component": "registry"}),
        ("release.yml", {}),
        ("deploy.yml", {}),
        ("operations.yml", {"action": "health"}),
    ]


def dispatch(workflow, inputs, deadline):
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    correlation = (
        "lifecycle-"
        + os.environ["GITHUB_RUN_ID"]
        + "-"
        + inputs.get("action", inputs.get("component", "run"))
    )
    current = gh("api", f"repos/{repo}/commits/main")["sha"]
    if workflow == "execution.yml" and inputs.get("action") == "stop":
        sha = current  # Retirement must survive a main update during setup.
    elif current != sha:
        raise ValueError("Main moved; refusing to mix source revisions")
    args = [
        "workflow",
        "run",
        workflow,
        "--repo",
        repo,
        "--ref",
        "main",
        "-f",
        "validation_only=false",
        "-f",
        f"orchestration_id={correlation}",
    ]
    for name, value in inputs.items():
        args += ["-f", f"{name}={value}"]
    gh(*args)
    title = workflow.removesuffix(".yml") + " " + correlation
    active_run = None
    while time.monotonic() < deadline:
        runs = gh(
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--commit",
            sha,
            "--event",
            "workflow_dispatch",
            "--limit",
            "50",
            "--json",
            "databaseId,displayTitle,headSha,status,conclusion",
        )
        matches = [
            r for r in runs if r["displayTitle"] == title and r["headSha"] == sha
        ]
        if len(matches) > 1:
            raise ValueError(
                "Ambiguous component dispatch; inspect runs before retrying"
            )
        if matches:
            run = matches[0]
            active_run = run["databaseId"]
            if run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise RuntimeError(
                        f"Component {workflow} failed; run {run['databaseId']}"
                    )
                print(
                    json.dumps(
                        {
                            "schema": 1,
                            "workflow": workflow,
                            "run_id": run["databaseId"],
                            "source_sha": sha,
                            "status": "passed",
                        }
                    ),
                    flush=True,
                )
                return run["databaseId"]
        time.sleep(10)
    if active_run:
        gh("run", "cancel", str(active_run), "--repo", repo)
        # Do not delete a runner while Terraform is still applying. A failure
        # to stop the child leaves retirement to the operator, preserving state.
        for _ in range(30):
            status = gh(
                "run", "view", str(active_run), "--repo", repo, "--json", "status"
            )
            if status["status"] == "completed":
                break
            time.sleep(10)
        else:
            raise ActiveChildError(
                "Child did not stop; preserve execution and investigate before retirement"
            )
    raise RuntimeError(
        "Lifecycle deadline reached; inspect queued/running component before retrying"
    )


class ActiveChildError(RuntimeError):
    """Do not destroy private execution underneath an active apply."""


def recreate(minutes):
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Lifecycle requires protected main")
    deadline = time.monotonic() + minutes * 60
    release = None
    safe_to_retire = True
    try:
        for workflow, inputs in stages():
            if workflow == "deploy.yml":
                inputs = inputs | {"release_run_id": str(release)}
            result = dispatch(workflow, inputs, deadline)
            if workflow == "release.yml":
                release = result
    except ActiveChildError:
        safe_to_retire = False
        raise
    finally:
        # Stop remains a separate protected workflow and runs from GitHub's
        # hosted runner, even when application networking failed. Give retirement
        # its own time allowance; this does not extend provider or load approval.
        if safe_to_retire:
            dispatch("execution.yml", {"action": "stop"}, time.monotonic() + 20 * 60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--minutes", type=int, default=50, choices=range(10, 56))
    args = parser.parse_args()
    if args.execute:
        recreate(args.minutes)
    else:
        print(
            json.dumps(
                {
                    "schema": 1,
                    "status": "validation_only",
                    "stages": stages(),
                    "finally": "execution.yml stop",
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error)) from None
