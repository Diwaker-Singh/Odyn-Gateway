"""Image metadata and build-input verification contracts."""

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "image_check_fixture", ROOT / "scripts/image-check.py"
)
IMAGE_CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGE_CHECK)


class ImageCheckTests(unittest.TestCase):
    def test_image_requires_linux_amd64_and_nonroot_user(self):
        metadata = {
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"User": "10001:10001"},
        }
        self.assertTrue(IMAGE_CHECK.valid_metadata(metadata))
        for override in (
            {"Architecture": "arm64"},
            {"Os": "windows"},
            {"Config": {"User": "0"}},
            {"Config": {"User": ""}},
        ):
            with self.subTest(override=override):
                self.assertFalse(IMAGE_CHECK.valid_metadata(metadata | override))

    def test_file_digest_matches_sha256(self):
        path = ROOT / "app/Dockerfile"
        self.assertEqual(
            IMAGE_CHECK.file_digest(path), hashlib.sha256(path.read_bytes()).hexdigest()
        )

    def test_runtime_image_copies_only_application_source(self):
        dockerfile = (ROOT / "app/Dockerfile").read_text()
        self.assertIn("COPY --chown=appuser:appuser app/src ./src", dockerfile)
        self.assertNotIn("COPY scripts", dockerfile)
        self.assertNotIn("COPY app/tests", dockerfile)


if __name__ == "__main__":
    unittest.main()
