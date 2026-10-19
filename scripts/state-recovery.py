"""Encrypted Terraform state archives and guarded, create-only backend recovery.

Recovery restores state bytes, not infrastructure or out-of-band provider keys.
It never overwrites a state blob or creates Azure resources.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
import zlib
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ODYNSTATE1\n"
MAX_PLAIN = 8 * 1024 * 1024
LABELS = {"application", "bootstrap", "registry"}


def command(args):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RECOVERY_ENCRYPTION_KEY", "ENCRYPTED_RECOVERY_B64")
    }
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=90, check=True, env=environment
    )
    return result.stdout


def az(*args):
    return json.loads(
        command(["az", *args, "--only-show-errors", "-o", "json"]) or "null"
    )


def encryption_key():
    key = base64.b64decode(os.environ["RECOVERY_ENCRYPTION_KEY"], validate=True)
    if len(key) != 32:
        raise ValueError("A 256-bit recovery key is required")
    return key


def validate_payload(payload, subscription):
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or payload.get("subscription") != subscription
    ):
        raise ValueError("Archive destination/version mismatch")
    if not isinstance(payload.get("states"), dict) or set(payload["states"]) != LABELS:
        raise ValueError("All three state snapshots are required")
    states = {}
    for label, value in payload["states"].items():
        raw = base64.b64decode(value, validate=True)
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or state.get("version") != 4
            or not isinstance(state.get("resources"), list)
            or not state["resources"]
        ):
            raise ValueError("Expected a nonempty Terraform v4 state")
        if type(state.get("serial")) is not int or state["serial"] < 0:
            raise ValueError("Invalid state serial")
        uuid.UUID(state["lineage"])
        for resource in state["resources"]:
            if not isinstance(resource, dict) or not isinstance(
                resource.get("instances", []), list
            ):
                raise TypeError("Invalid Terraform resource entry")
            for instance in resource.get("instances", []):
                if not isinstance(instance, dict) or not isinstance(
                    instance.get("attributes", {}), dict
                ):
                    raise TypeError("Invalid Terraform resource attributes")
                identifier = instance.get("attributes", {}).get("id", "")
                if isinstance(identifier, str) and identifier.lower().startswith(
                    "/subscriptions/"
                ):
                    parts = identifier.split("/")
                    if parts[2].lower() != subscription.lower():
                        raise ValueError("State contains another subscription")
                    if (
                        len(parts) > 4
                        and parts[3].lower() == "resourcegroups"
                        and not parts[4].startswith("rg-odyn-gateway-")
                    ):
                        raise ValueError("State contains a foreign resource group")
        states[label] = raw
    return states


def seal(payload, subscription):
    validate_payload(payload, subscription)
    plain = json.dumps(payload, separators=(",", ":")).encode()
    if len(plain) > MAX_PLAIN:
        raise ValueError("Recovery archive too large")
    nonce = os.urandom(12)
    return (
        MAGIC
        + nonce
        + AESGCM(encryption_key()).encrypt(nonce, gzip.compress(plain, mtime=0), MAGIC)
    )


def open_archive(raw, expected_hash, subscription):
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ValueError("Expected a SHA-256 archive digest")
    if hashlib.sha256(raw).hexdigest() != expected_hash or len(raw) > MAX_PLAIN:
        raise ValueError("Archive digest/size mismatch")
    if not raw.startswith(MAGIC):
        raise ValueError("Unknown archive format")
    body = raw[len(MAGIC) :]
    compressed = AESGCM(encryption_key()).decrypt(body[:12], body[12:], MAGIC)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    plain = decoder.decompress(compressed, MAX_PLAIN + 1)
    if len(plain) > MAX_PLAIN or not decoder.eof or decoder.unused_data:
        raise ValueError("Invalid or excessive compressed archive")
    return validate_payload(json.loads(plain), subscription)


def checked_backends(component, subscription):
    labels = ("bootstrap", "registry") if component == "foundation" else (component,)
    if not set(labels) <= LABELS:
        raise ValueError("Unknown recovery component")
    configuration = json.loads(os.environ["STATE_BACKENDS_JSON"])
    result = {}
    for label in labels:
        backend = configuration[label]
        if backend.get("use_azuread_auth") is not True or any(
            field in backend for field in ("access_key", "sas_token", "client_secret")
        ):
            raise ValueError("Recovery requires Entra-only backend access")
        if backend.get("subscription_id", subscription) != subscription:
            raise ValueError("Backend subscription mismatch")
        if backend.get("tenant_id") != os.environ["AZURE_TENANT_ID"]:
            raise ValueError("Backend tenant mismatch")
        if not re.fullmatch(
            r"rg-odyn-gateway-(?:tfstate|automation)(?:-[a-z0-9-]+)?",
            backend["resource_group_name"],
        ):
            raise ValueError("Use a dedicated state group")
        if not re.fullmatch(r"[a-z0-9]{3,24}", backend["storage_account_name"]):
            raise ValueError("Invalid state account")
        if not re.fullmatch(r"[a-z0-9-]{3,63}", backend["container_name"]):
            raise ValueError("Invalid state container")
        if (
            not re.fullmatch(r"[a-zA-Z0-9_./-]+", backend["key"])
            or ".." in backend["key"]
        ):
            raise ValueError("Invalid state key")
        result[label] = backend
    destinations = {
        (b["storage_account_name"], b["container_name"], b["key"])
        for b in result.values()
    }
    if len(destinations) != len(result):
        raise ValueError("State destinations must be distinct")
    return result


def blob_args(backend):
    return (
        "--account-name",
        backend["storage_account_name"],
        "--container-name",
        backend["container_name"],
        "--name",
        backend["key"],
        "--auth-mode",
        "login",
    )


def restore(states, component, confirmation):
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    backends = checked_backends(component, subscription)
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Recovery requires protected main")
    if confirmation != f"restore {subscription} {component}":
        raise ValueError("Recovery confirmation mismatch")
    account = az("account", "show")
    if (
        not isinstance(account, dict)
        or account.get("id") != subscription
        or account.get("tenantId") != os.environ["AZURE_TENANT_ID"]
        or account.get("user", {}).get("type") != "servicePrincipal"
        or account["user"].get("name", "").lower()
        != os.environ["AZURE_CLIENT_ID"].lower()
    ):
        raise ValueError("Recovery identity/destination mismatch")
    # Inspect all selected targets before writing any. Missing connectivity is
    # an error, never evidence that a blob is absent.
    for backend in backends.values():
        metadata = az(
            "storage",
            "account",
            "show",
            "-g",
            backend["resource_group_name"],
            "-n",
            backend["storage_account_name"],
        )
        expected = (
            f"/subscriptions/{subscription}/resourceGroups/"
            f"{backend['resource_group_name']}/providers/Microsoft.Storage/"
            f"storageAccounts/{backend['storage_account_name']}"
        )
        if metadata.get("id", "").lower() != expected.lower():
            raise ValueError("State account resource ID mismatch")
        if metadata.get("allowSharedKeyAccess") is not False:
            raise ValueError("State shared-key access must be disabled")
        if (
            metadata.get("allowBlobPublicAccess") is not False
            or metadata.get("networkRuleSet", {}).get("defaultAction") != "Deny"
            or metadata.get("minimumTlsVersion") != "TLS1_2"
            or metadata.get("enableHttpsTrafficOnly") is not True
        ):
            raise ValueError("State account must enforce private access and TLS")
        exists = az("storage", "blob", "exists", *blob_args(backend)).get("exists")
        if exists is not False:
            raise ValueError(
                "Refusing to overwrite or assume an existing state is absent"
            )
    with tempfile.TemporaryDirectory() as directory:
        for label, backend in backends.items():
            path = Path(directory) / (label + ".tfstate")
            path.write_bytes(states[label])
            path.chmod(0o600)
            az(
                "storage",
                "blob",
                "upload",
                *blob_args(backend),
                "--file",
                str(path),
                "--overwrite",
                "false",
                "--if-none-match",
                "*",
            )
            verify = Path(directory) / (label + ".verified")
            az(
                "storage",
                "blob",
                "download",
                *blob_args(backend),
                "--file",
                str(verify),
                "--overwrite",
            )
            if verify.read_bytes() != states[label]:
                raise ValueError("Restored state failed byte verification")
    return list(backends)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "archive", "verify", "restore"))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256")
    parser.add_argument("--directory", type=Path)
    parser.add_argument(
        "--component", choices=("foundation", "application", "bootstrap", "registry")
    )
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "create":
        if not args.directory or args.directory.stat().st_mode & 0o077:
            raise ValueError("Use an owner-only directory containing the three states")
        payload = {
            "schema": 1,
            "subscription": os.environ["AZURE_SUBSCRIPTION_ID"],
            "states": {
                label: base64.b64encode(
                    (args.directory / (label + ".tfstate")).read_bytes()
                ).decode()
                for label in LABELS
            },
        }
        raw = seal(payload, os.environ["AZURE_SUBSCRIPTION_ID"])
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        with args.archive.open("xb") as stream:
            stream.write(raw)
        print(
            json.dumps(
                {
                    "schema": 1,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "status": "encrypted",
                }
            )
        )
        return
    if args.action == "archive":
        encoded = os.environ.get("ENCRYPTED_RECOVERY_B64")
        if encoded is None:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            encoded = event["inputs"]["encrypted_snapshot"]
        if len(encoded) > 60000:
            raise ValueError("Use an artifact-producing backup for larger archives")
        raw = base64.b64decode(encoded, validate=True)
    else:
        raw = args.archive.read_bytes()
    states = open_archive(raw, args.sha256 or "", os.environ["AZURE_SUBSCRIPTION_ID"])
    if args.action == "archive":
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        args.archive.write_bytes(raw)
        args.archive.chmod(0o600)
    labels = sorted(states)
    if args.action == "restore":
        if not args.component:
            raise ValueError("Select a recovery component")
        labels = restore(states, args.component, args.confirmation)
    print(
        json.dumps(
            {
                "schema": 1,
                "operation": args.action,
                "states": labels,
                "status": "passed",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        InvalidTag,
        subprocess.SubprocessError,
        zlib.error,
    ):
        raise SystemExit(
            "State recovery stopped; sensitive details suppressed."
        ) from None
