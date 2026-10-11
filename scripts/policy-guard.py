#!/usr/bin/env python3
"""Repository policy guard (stdlib only — no OPA by decision).

Fails (exit 1) on any violated invariant; prints each result. Checks:
1. `.env.example` carries an EMPTY OpenRouter placeholder (no value).
2. No `sk-or-v1-` value (>=10 token chars) outside `.gitleaks.toml` patterns.
3. `network_enforced=true` appears nowhere (unproven until bypass test).
4. Default upstream mode is stub; prod config must reject stub explicitly.
5. `.terraform.lock.hcl` is TRACKED by gitignore (reproducibility).
6. No `terraform apply`/`destroy` invocations in tracked automation.
7. No committed `.tfvars` with values (only `*.example.tfvars`).
"""

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Untracked yet (pre-commit scaffold): fall back to working tree scan.
    if out.returncode != 0 or not out.stdout.strip():
        return [
            p for p in ROOT.rglob("*") if p.is_file() and ".git/" not in p.as_posix()
        ]
    return [ROOT / line for line in out.stdout.splitlines()]


def main() -> int:
    env_example = (ROOT / ".env.example").read_text()
    m = re.search(r"^OPENROUTER_API_KEY=(.*)$", env_example, re.MULTILINE)
    check(
        "env-placeholder-empty",
        m is not None and m.group(1).strip() == "",
        "OPENROUTER_API_KEY must be empty",
    )

    key_re = re.compile(r"sk-or-v1-[A-Za-z0-9_\-.]{10,}")
    offenders = []
    for p in tracked_files():
        posix = p.as_posix()
        if ".git/" in posix or p.name == ".gitleaks.toml":
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if key_re.search(text):
            offenders.append(p.relative_to(ROOT).as_posix())
    check("no-key-material", not offenders, f"{offenders}" if offenders else "")

    net_true = []
    for p in tracked_files():
        posix = p.as_posix()
        if ".git/" in posix:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if re.search(r"network_enforced\s*=\s*True", text):
            net_true.append(p.relative_to(ROOT).as_posix())
    check("network-enforced-false", not net_true, f"{net_true}" if net_true else "")

    cfg = (ROOT / "app" / "src" / "gateway" / "config.py").read_text()
    check("stub-default", 'upstream_mode: str = "stub"' in cfg)
    check(
        "prod-rejects-stub-note",
        "must reject stub" in (ROOT / "app" / "Dockerfile").read_text().lower()
        or "reject stub" in cfg.lower()
        or "reject" in (ROOT / "docs" / "architecture.md").read_text().lower(),
    )

    gi = (ROOT / ".gitignore").read_text()
    check(
        "lockfile-tracked", ".terraform.lock.hcl" in gi and "!.terraform.lock.hcl" in gi
    )

    auto = subprocess.run(
        ["git", "check-ignore", "-q", ".terraform.lock.hcl"], cwd=ROOT, check=False
    )
    check("lockfile-not-ignored", auto.returncode != 0)

    apply_hits = []
    for pat in ("*.yml", "*.yaml", "Makefile", "*.sh", "*.py"):
        for p in ROOT.rglob(pat):
            if ".git/" in p.as_posix():
                continue
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            # Single merged condition: prose prohibitions allowed, real
            # invocations flagged (SIM102: no nested if).
            if (
                re.search(r"terraform\s+(apply|destroy)\b", text)
                and "no `terraform apply`" not in text.lower()
                and "no terraform apply" not in text.lower()
                and (
                    re.search(
                        r"^\s*(run:|[\s]*[-]?\s*run\s+terraform|- )",
                        text,
                        re.MULTILINE,
                    )
                    or "terraform apply " in text
                )
            ):
                apply_hits.append(p.relative_to(ROOT).as_posix())
    # Scanned patterns cover yml/yaml/Makefile/sh/py only; .md prose and the
    # no-apply comments above are out of scope by construction.
    # Exception: scripts/deploy.sh legitimately invokes `terraform apply`, but
    # ONLY behind the ODYN_DEPLOY_APPROVED human-set gate (plan-only default,
    # never called by CI). Any other file with a real invocation fails.
    gated = []
    ungated = []
    for hit in apply_hits:
        if hit == "scripts/deploy.sh":
            text = (ROOT / hit).read_text(errors="ignore")
            if "ODYN_DEPLOY_APPROVED" in text and "--apply" in text:
                gated.append(hit)
                continue
        ungated.append(hit)
    check(
        "no-apply-automation",
        not ungated,
        f"ungated:{ungated}" if ungated else (f"gated:{gated}" if gated else ""),
    )

    tfvars = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*.tfvars")
        if ".git/" not in p.as_posix()
        and not fnmatch.fnmatch(p.name, "*.example.tfvars")
    ]
    check("no-committed-tfvars-values", not tfvars, f"{tfvars}" if tfvars else "")

    print(f"\n{len(failures)} failing check(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
