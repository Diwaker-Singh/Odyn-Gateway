"""Stable source fingerprint used by local image and release checks."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

SOURCE_SUFFIXES = {
    ".py",
    ".tf",
    ".tftpl",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".in",
    ".lock",
    ".hcl",
    ".sh",
    ".js",
    ".html",
    ".css",
    ".md",
}


def source_files(root: Path) -> Iterator[Path]:
    for directory in (
        "app",
        "infra",
        "scripts",
        ".github",
        "loadtest",
        "docs",
        "local-ui",
    ):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or any(
                part in {".terraform", "__pycache__", "dist", "build"}
                for part in path.relative_to(root).parts
            ):
                continue
            if path.suffix not in SOURCE_SUFFIXES and path.name != "Dockerfile":
                continue
            yield path
    for name in (
        "README.md",
        "Makefile",
        ".dockerignore",
        ".gitignore",
        ".gitleaks.toml",
        ".pre-commit-config.yaml",
        ".env.example",
        ".tool-versions",
    ):
        path = root / name
        if path.is_file():
            yield path


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()
