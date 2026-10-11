# Identity provider setup

The gateway validates access tokens issued for its API. Register an API
resource and separate human and confidential clients in the chosen OIDC
provider. Use the provider's supported libraries for login and token
acquisition; the gateway does not collect user passwords.

## API resource

- Configure a stable issuer and audience for access tokens.
- Define a delegated permission for user-driven access and a distinct
  application role for workload access.
- Keep delegated scopes and application roles separate. A token's `roles`
  claim must not substitute for a missing delegated scope.
- Grant only the permission required by this service.

## Human client

Use a public/native client with authorization code and PKCE (S256). Register
the exact loopback redirect URI used by the local demo. Do not create a client
secret for a public client. Request only the gateway's delegated permission and
the OIDC scopes required by the maintained authentication library.

## Workload client

Use a confidential client with a short-lived certificate credential where the
provider supports it. Store the private key in a secret manager or workload
identity system; upload only the public certificate. Assign only the gateway
application role. Do not grant broad directory permissions.

## Gateway configuration

Set issuer, audience and trusted JWKS endpoint through deployment configuration.
The gateway supports RS256 and validates issuer, audience, expiry, not-before,
delegated scope and application role. It refreshes trusted keys within bounded
timeouts and fails closed after the trusted stale-key window expires.

The local demo reads client configuration from an owner-only ignored file under
`.secrets/sso/`. Tokens and private keys must never be committed, printed, added
to browser storage, or passed as command-line arguments.

Access tokens remain valid until expiry unless the identity provider and API
use an immediate-revocation mechanism supported by the API. Removing an
assignment or consent generally affects new token issuance; it should not be
presented as immediate invalidation of already-issued tokens. Define credential
rotation, emergency revocation and app-registration ownership for each
deployment.
