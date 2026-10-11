# Hosted and local chat UI

`hosted-ui/` is a static SPA intended for GitHub Pages. It uses Microsoft
Entra authorization code with PKCE through MSAL Browser. The browser stores the
access token in session storage and sends it to the configured gateway. The
OpenRouter key remains in Key Vault and is never included in this site.

## Connection settings

The site loads public settings from `config.json` at runtime:

- `tenant_id`: Entra tenant GUID.
- `client_id`: SPA application/client GUID.
- `scope`: delegated API scope, normally `api://<api-app-id>/chat.invoke`.
- `gateway_url`: full HTTPS URL ending in `/v1/chat`.

These are public client identifiers, not secrets. The Connection settings form
can override them for the current browser tab; use only a gateway you trust,
because the bearer token is sent to that URL. Do not place client secrets,
provider keys, or certificates in the site.

## GitHub Pages

The `.github/workflows/pages.yml` workflow builds and publishes the project
site at `https://diwaker-singh.github.io/Odyn-Gateway/`. Configure these GitHub
Actions repository variables (not secrets):

| Variable | Value |
| --- | --- |
| `ODYN_ENTRA_TENANT_ID` | Tenant GUID |
| `ODYN_ENTRA_CLIENT_ID` | Human SPA client GUID |
| `ODYN_API_SCOPE` | `api://<api-app-id>/chat.invoke` |
| `ODYN_GATEWAY_CHAT_URL` | `https://<Front-Door-host>/v1/chat` |

Register `https://diwaker-singh.github.io/Odyn-Gateway/` as a **Single-page
application** redirect URI on the human client registration. The API gateway
must allow the exact origin `https://diwaker-singh.github.io` in APIM CORS and
continue to require the existing Entra scope. Sign-in eligibility should be
limited to the intended demo audience, with gateway per-user/global controls
left enabled.

## Local preview

Create an ignored local runtime config from the example and fill in the public
values for the local SPA registration and target gateway:

```sh
cd hosted-ui
cp public/config.example.json public/config.json
npm ci
npm test
npm run dev -- --host 127.0.0.1
```

Register the displayed local dev URL, typically `http://localhost:5173/`, as a
SPA redirect URI for that local client. For a direct browser call to the cloud
gateway, add that exact loopback origin to APIM CORS. Terraform accepts HTTP
only for `localhost`, `127.0.0.1`, and `[::1]`, and requires HTTPS for every
other origin. Do not add a broad wildcard. For local gateway testing, configure
the local gateway's CORS allowlist and its own issuer/audience settings. The
existing Python loopback UI remains available for a local proxy flow:

```sh
.venv/bin/python scripts/local_chat_ui.py
```

## Safety and operation

- The hosted and local demo UIs ask for one bounded request at a time and cap
  responses at 512 tokens. This is a deliberate demo UI choice to bound request
  size and possible provider cost; response length is not a core gateway
  feature. The gateway itself permits a configured maximum of 1,024 tokens.
- The gateway disables hidden model reasoning for these chat requests so the
  token budget remains available for visible answer text.
- Chat history is held in page memory only and is cleared on reload or sign-out.
- The app asks for a delegated API token only; it does not request Graph scopes.
- A sign-in alone should not make every tenant account eligible for provider
  spend. Restrict the API to the intended demo users and keep rate and daily
  budget enforcement active.
- The static site has no backend and cannot protect a provider credential.
  Never add the OpenRouter key to source, build variables, or browser storage.
