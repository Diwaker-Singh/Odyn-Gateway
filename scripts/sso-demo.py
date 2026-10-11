"""Approval-gated real Entra PKCE/client-credentials demonstration.

Reads only ignored private configuration. Token values never reach stdout,
evidence or Terraform. MSAL handles PKCE, nonce/state and certificate assertions.
"""

import argparse
import json
import os
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import msal

ROOT = Path(__file__).resolve().parent.parent


def validate(config):
    for field in ("tenant_id", "api_app_id", "human_client_id", "machine_client_id"):
        import uuid

        uuid.UUID(config[field])
    return config


def human(config):
    application = msal.PublicClientApplication(
        config["human_client_id"],
        authority="https://login.microsoftonline.com/" + config["tenant_id"],
        timeout=10,
        enable_pii_log=False,
    )
    flow = application.initiate_auth_code_flow(
        ["api://" + config["api_app_id"] + "/chat.invoke"],
        redirect_uri="http://localhost:8400",
        response_mode="query",
    )
    response = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path != "/":
                self.send_error(404)
                return
            response.update(
                {
                    key: values[0]
                    for key, values in parse_qs(urlsplit(self.path).query).items()
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Sign-in callback received. Return to the terminal.")

        def log_message(self, *_):
            pass  # Callback contains a one-time authorization code; never log it.

    with HTTPServer(("127.0.0.1", 8400), Handler) as server:
        server.timeout = 180
        if not webbrowser.open(flow["auth_uri"]):
            raise ValueError("browser unavailable")
        server.handle_request()
    if not response:
        raise ValueError("sign-in timed out")
    return application.acquire_token_by_auth_code_flow(flow, response)


def machine(config):
    key = ROOT / ".secrets" / "sso" / "machine.key"
    if key.stat().st_mode & 0o077:
        raise ValueError("machine key permissions must be private")
    application = msal.ConfidentialClientApplication(
        config["machine_client_id"],
        authority="https://login.microsoftonline.com/" + config["tenant_id"],
        client_credential={
            "private_key": key.read_text(),
            "thumbprint": config["machine_certificate_thumbprint"],
        },
        timeout=10,
        enable_pii_log=False,
    )
    return application.acquire_token_for_client(
        scopes=["api://" + config["api_app_id"] + "/.default"]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flow", choices=("human", "machine"))
    parser.add_argument("--approved-entra-demo", action="store_true")
    args = parser.parse_args()
    if not args.approved_entra_demo:
        parser.error("real Entra authentication demonstration approval required")
    directory = ROOT / ".secrets/sso"
    config_path = directory / "config.json"
    if config_path.stat().st_mode & 0o077:
        raise ValueError("SSO configuration permissions must be private")
    config = validate(json.loads(config_path.read_text()))
    result = human(config) if args.flow == "human" else machine(config)
    if "access_token" not in result:
        raise ValueError("Entra did not issue an access token")
    os.umask(0o077)
    destination = directory / (args.flow + "-token.json")
    descriptor, temporary = tempfile.mkstemp(prefix=".token-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(
                {
                    "access_token": result["access_token"],
                    "expires_in": result.get("expires_in"),
                },
                stream,
            )
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(
        args.flow
        + " access token acquired in ignored private storage; gateway proof remains."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError):
        raise SystemExit(
            "SSO demonstration failed; sensitive diagnostics suppressed."
        ) from None
