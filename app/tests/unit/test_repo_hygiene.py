"""Repo hygiene: ignore/track assertions + secret-placeholder checks.

Runs `git check-ignore` (skipped if git unavailable). Complements
`scripts/policy-guard.py`, which CI also executes.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def ignored(path: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    return r.returncode == 0


@unittest.skipUnless(shutil.which("git"), "git required")
class RepoHygieneTests(unittest.TestCase):
    def test_secret_filenames_ignored(self):
        for p in (
            ".env",
            ".env.local",
            "x.tfstate",
            "y.tfstate.backup",
            "p.tfplan",
            "terraform.tfvars",
            "prod.auto.tfvars",
            "k.pem",
            "k.key",
            ".coverage",
        ):
            with self.subTest(path=p):
                self.assertTrue(ignored(p), p)

    def test_examples_and_sources_trackable(self):
        for p in (
            ".env.example",
            "a.example.tfvars",
            "README.md",
            "app/src/gateway/api.py",
            ".github/workflows/ci.yml",
        ):
            with self.subTest(path=p):
                self.assertFalse(ignored(p), p)

    def test_lockfile_trackable(self):
        self.assertFalse(ignored(".terraform.lock.hcl"))

    def test_env_placeholder_empty(self):
        text = (ROOT / ".env.example").read_text()
        m = re.search(r"^OPENROUTER_API_KEY=(.*)$", text, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "")

    def test_deploy_script_gated(self):
        text = (ROOT / "scripts" / "deploy.py").read_text()
        self.assertIn("ODYN_DEPLOY_APPROVED", text)
        self.assertIn('default="preflight"', text)
        self.assertIn("ODYN_PLAN_APPROVED", text)
        self.assertIn("ODYN_REVIEWED_PLAN_SHA256", text)
        # Never destroys; never references live secrets.
        self.assertNotIn("terraform destroy", text)
        self.assertNotIn("sk-or-v1-", text)

    def test_provider_key_script_hygiene(self):
        text = (ROOT / "scripts" / "set-provider-key.sh").read_text()
        self.assertIn("--placeholder", text)
        self.assertIn("read -r -s", text)  # hidden input
        self.assertIn("--file", text)  # value via file, not argv
        self.assertNotIn("sk-or-v1-", text)


if __name__ == "__main__":
    unittest.main()
