"""Decrypt one delivery diagnostic artifact and print only a safe failure class."""

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from delivery import classify_init_failure


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    key = base64.b64decode(os.environ["PLAN_ENCRYPTION_KEY"], validate=True)
    if len(key) != 32 or args.artifact.is_symlink() or not args.artifact.is_file():
        raise SystemExit("Invalid diagnostic input")
    ciphertext = args.artifact.read_bytes()
    try:
        payload = json.loads(
            AESGCM(key).decrypt(
                ciphertext[:12], ciphertext[12:], b"odyn-delivery-diagnostics-v1"
            )
        )
    except Exception as error:
        raise SystemExit("Diagnostic authentication failed") from error
    if payload.get("schema") != 1 or not isinstance(payload.get("logs"), dict):
        raise SystemExit("Invalid diagnostic schema")
    init_log = payload["logs"].get("init.log", "")
    if not isinstance(init_log, str):
        raise SystemExit("Invalid diagnostic log")
    report = {
        "schema": 1,
        "source": payload.get("source"),
        "run": payload.get("run"),
        "stage": "terraform-init",
        "category": classify_init_failure(init_log),
        "raw_logs_emitted": False,
    }
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
