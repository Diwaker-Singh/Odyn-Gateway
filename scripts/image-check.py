"""Verify reviewed Azure image platform and isolated nonroot application smoke."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    from source_fingerprint import source_digest
except ModuleNotFoundError:  # loaded via importlib without scripts/ on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from source_fingerprint import source_digest

ROOT = Path(__file__).resolve().parent.parent


def valid_metadata(value):
    user = value.get("Config", {}).get("User", "").split(":")[0]
    return (
        value.get("Os") == "linux"
        and value.get("Architecture") == "amd64"
        and user.isdigit()
        and int(user) != 0
    )


def source_tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file() or "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        digest.update(item.relative_to(path).as_posix().encode() + b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--collector", required=True)
    args = parser.parse_args()
    checks = []
    for name, image in (("gateway", args.gateway), ("collector", args.collector)):
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=True,
        )
        metadata = json.loads(result.stdout)[0]
        checks.append(
            {
                "id": name + "-platform",
                "status": "passed" if valid_metadata(metadata) else "failed",
                "os": metadata.get("Os"),
                "architecture": metadata.get("Architecture"),
                "image_id": metadata.get("Id"),
            }
        )
    if all(c["status"] == "passed" for c in checks):
        app_source_digest = source_tree_digest(ROOT / "app/src")
        digest_program = """import hashlib, json
from pathlib import Path
root = Path("/srv/app/src")
digest = hashlib.sha256()
for item in sorted(root.rglob("*")):
    if not item.is_file() or "__pycache__" in item.parts or item.suffix == ".pyc":
        continue
    digest.update(item.relative_to(root).as_posix().encode() + b"\\0")
    digest.update(item.read_bytes())
print(json.dumps({"app_src_sha256": digest.hexdigest()}))
"""
        digest_result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--platform=linux/amd64",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint",
                "python",
                args.gateway,
                "-c",
                digest_program,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        image_app_source_digest = None
        if digest_result.returncode == 0:
            image_app_source_digest = json.loads(digest_result.stdout)["app_src_sha256"]
        checks.append(
            {
                "id": "gateway-app-source-parity",
                "status": "passed"
                if image_app_source_digest == app_source_digest
                else "failed",
                "workspace_app_src_sha256": app_source_digest,
                "image_app_src_sha256": image_app_source_digest,
            }
        )
        build_input_program = """import hashlib, json
from pathlib import Path
paths = {
    "runtime_lock": Path("/srv/app/requirements.lock"),
}
print(json.dumps({name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}))
"""
        input_result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--platform=linux/amd64",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint",
                "python",
                args.gateway,
                "-c",
                build_input_program,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        image_inputs = None
        if input_result.returncode == 0:
            image_inputs = json.loads(input_result.stdout)
        workspace_inputs = {
            "runtime_lock": file_digest(ROOT / "app/requirements-runtime.lock"),
        }
        checks.append(
            {
                "id": "gateway-build-input-parity",
                "status": "passed" if image_inputs == workspace_inputs else "failed",
                "workspace_sha256": workspace_inputs,
                "image_sha256": image_inputs,
                "dockerfile_sha256": file_digest(ROOT / "app/Dockerfile"),
            }
        )
        program = """import os
from fastapi.testclient import TestClient
from gateway.web import create_app
assert os.getuid() == 10001
from gateway.config import GatewayConfig
fixture_tenant = "00000000-0000-0000-0000-000000000001"
config = GatewayConfig.from_env({"GATEWAY_ENVIRONMENT": "azure", "UPSTREAM_MODE": "stub", "IDP_ISSUER_URL": f"https://login.microsoftonline.com/{fixture_tenant}/v2.0", "IDP_AUDIENCE": "fixture-api", "JWKS_URL": f"https://login.microsoftonline.com/{fixture_tenant}/discovery/v2.0/keys", "AZURE_CLIENT_ID": "fixture-mi", "TABLE_ENDPOINT": "https://example.table.core.windows.net/"})
assert config.table_endpoint == "https://example.table.core.windows.net"
# Construction uses the real Azure factory; no data-plane request or credential fallback.
azure_app = create_app(config=config)
assert azure_app is not None
with TestClient(create_app()) as client:
 assert client.get("/healthz").status_code == 200
 assert client.get("/readyz").status_code == 200
 assert client.post("/v1/chat", json={}).status_code == 401
print("isolated ASGI smoke passed")
"""
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--platform=linux/amd64",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--entrypoint",
                "python",
                args.gateway,
                "-c",
                program,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        checks.append(
            {
                "id": "gateway-isolated-smoke",
                "status": "passed" if result.returncode == 0 else "failed",
            }
        )
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--platform=linux/amd64",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                args.collector,
                "--help",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        checks.append(
            {
                "id": "collector-executable",
                "status": "passed" if result.returncode == 0 else "failed",
            }
        )
    report = {"source_sha256": source_digest(ROOT), "checks": checks}
    output = ROOT / ".artifacts"
    output.mkdir(parents=True, exist_ok=True)
    (output / "image-platform-results.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(
        "Image platform/smoke:",
        sum(c["status"] == "passed" for c in checks),
        "of",
        len(checks),
        "passed",
    )
    return 0 if all(c["status"] == "passed" for c in checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit(
            "Image verification blocked; no private diagnostics printed."
        ) from None
