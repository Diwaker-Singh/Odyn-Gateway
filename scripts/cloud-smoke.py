"""Bounded public edge checks; never sends a valid chat or provider request."""

import argparse
import json
from urllib.parse import urlsplit

import httpx


def check_edge(chat_url, origin, transport=None):
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
        response = client.get(chat_url.removesuffix("/v1/chat") + "/v1/healthz")
        results["health"] = response.status_code == 200
        results["hsts"] = "max-age=" in response.headers.get(
            "strict-transport-security", ""
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
    parser.add_argument("--chat-url", required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    try:
        results = check_edge(args.chat_url, args.origin)
        print(json.dumps({"scope": "public_edge_no_provider", "checks": results}))
        return 0 if all(results.values()) else 1
    except (ValueError, httpx.HTTPError):
        print("Edge smoke failed; response details suppressed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
