# Local chat demo

From the repository root, start the loopback-only browser client:

```sh
.venv/bin/python scripts/local_chat_ui.py
```

The server binds to `127.0.0.1:8400` and opens the registered OIDC redirect
URI. Set `ODYN_GATEWAY_URL` to the HTTPS gateway origin, sign in with the
configured human client, then send a message. Stop the server with Ctrl-C.
Configure issuer and client IDs in the ignored
`.secrets/sso/config.json`; see [identity setup](identity-setup.md).

The browser talks to a local Python proxy. The access token stays in the
server process and is sent to the gateway in an authorization header. The
session cookie is HttpOnly, same-site, and scoped to the loopback origin. Chat
history stays in browser memory and clears on reload. Requests are bounded and
not retried automatically after ambiguous failures. In live mode, each send
may incur provider charges.

This demo is not a hosted frontend. Hosting it requires a separate client
registration, origin/cookie review, deployment configuration, and production
security review.
