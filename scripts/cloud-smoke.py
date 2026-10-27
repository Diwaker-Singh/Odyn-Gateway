"""Bounded public edge checks; never sends a valid chat or provider request."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def deployment_chat_url(group):
    """Resolve only the endpoint owned by the selected Azure deployment."""
    if not re.fullmatch(r"rg-odyn-gateway-[a-z0-9]{2,8}", group):
        raise ValueError("Expected a dedicated application group")
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    profile = "fd-" + group.removeprefix("rg-")
    result = subprocess.run(
        [
            "az",
            "afd",
            "endpoint",
            "list",
            "--subscription",
            subscription,
            "--resource-group",
            group,
            "--profile-name",
            profile,
            "--only-show-errors",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    endpoints = json.loads(result.stdout)
    if len(endpoints) != 1:
        raise ValueError("Expected exactly one deployment endpoint")
    endpoint = endpoints[0]
    expected = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Cdn/profiles/{profile}/afdEndpoints/{profile}"
    host = endpoint.get("hostName", "")
    if (
        endpoint.get("id", "").lower() != expected.lower()
        or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.azurefd\.net", host)
        or endpoint.get("enabledState") != "Enabled"
    ):
        raise ValueError("Endpoint does not match the selected deployment")
    return f"https://{host}/v1/chat"


def check_edge(
    chat_url,
    origin,
    transport=None,
    health_attempts=60,
    health_interval_seconds=20,
):
    parsed = urlsplit(chat_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1/chat"
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Expected a trusted HTTPS /v1/chat URL")
    results = {}
    with httpx.Client(transport=transport, follow_redirects=False, timeout=8) as client:
        health_url = chat_url.removesuffix("/v1/chat") + "/v1/healthz"
        results["health"] = False
        # AFD can accept an ARM route update before the global edge deployment
        # is visible. Retry bounded transient misses during that propagation
        # window; fail immediately on policy/configuration statuses like 401.
        for attempt in range(health_attempts):
            try:
                response = client.get(health_url)
            except httpx.HTTPError:
                if attempt + 1 == health_attempts:
                    break
            else:
                if response.status_code == 200:
                    results["health"] = True
                    break
                if response.status_code not in (404, 500, 502, 503, 504):
                    break
            if attempt + 1 < health_attempts:
                time.sleep(health_interval_seconds)
        results["hsts"] = (
            "max-age=" in response.headers.get("strict-transport-security", "")
            if "response" in locals()
            else False
        )
        # Empty unauthenticated input cannot be an authorized provider request.
        response = client.post(chat_url, json={})
        results["unauthenticated_rejection"] = response.status_code == 401
        response = client.options(
            chat_url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        results["exact_origin_cors"] = (
            response.status_code in (200, 204)
            and response.headers.get("access-control-allow-origin") == origin
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--chat-url")
    target.add_argument("--resource-group")
    parser.add_argument("--origin")
    parser.add_argument("--export-endpoint", type=Path)
    args = parser.parse_args()
    try:
        url = (
            deployment_chat_url(args.resource_group)
            if args.resource_group
            else args.chat_url
        )
        if args.export_endpoint:
            if not args.resource_group:
                raise ValueError("Endpoint export requires Azure deployment resolution")
            with args.export_endpoint.open("a") as stream:
                stream.write(
                    f"CLOUD_CHAT_URL={url}\nHEALTH_URL={url.removesuffix('/v1/chat')}/v1/healthz\n"
                )
            return 0
        if not args.origin:
            raise ValueError("Client origin required")
        results = check_edge(url, args.origin)
        print(json.dumps({"scope": "public_edge_no_provider", "checks": results}))
        return 0 if all(results.values()) else 1
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, httpx.HTTPError):
        print("Edge smoke failed; response details suppressed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
