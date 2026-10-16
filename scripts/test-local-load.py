"""Run the bounded k6 scenario against a child loopback-only fixture server."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app/src"))
sys.path.insert(0, str(ROOT / "app/tests"))
from helpers import make_gateway, user_token


def main():
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    # Refuse to test an unrelated process that already owns the fixture port.
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8000))
    env = {**os.environ, "PYTHONPATH": "app/src:app/tests"}
    with (output / "stub-server.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "scripts/serve_stub.py"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError("Fixture server exited before readiness")
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8000/readyz", timeout=1
                    ) as response:
                        if response.status == 200:
                            if json.load(response).get("upstream_mode") != "stub":
                                raise RuntimeError("Load target is not in stub mode")
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Fixture server readiness timed out")
            gateway = make_gateway()
            env.update(
                GATEWAY_URL="http://127.0.0.1:8000/v1/chat",
                AUTH_TOKEN_A=user_token(gateway, oid="load-burst"),
                AUTH_TOKEN_B=user_token(gateway, oid="load-gentle"),
            )
            return subprocess.run(
                [
                    "k6",
                    "run",
                    "--no-usage-report",
                    "--summary-export",
                    str(output / "local-load-summary.json"),
                    "loadtest/k6-limits.js",
                ],
                cwd=ROOT,
                env=env,
                timeout=60,
                check=False,
            ).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
