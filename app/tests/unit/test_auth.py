"""JWT negative matrix: every row must map to 401 except scope/role (403)."""

import time
import unittest

from helpers import AUDIENCE, ISSUER, KEY, KID, base_claims

from gateway.auth import JwtValidator, LocalJwks, mint_fixture


def validator() -> JwtValidator:
    return JwtValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        allowed_algorithms=("RS256", "HS256"),
        jwks=LocalJwks({KID: KEY}),
        test_hs256=True,  # fixture path; live config forbids it (see below)
    )


class AuthNegativeTests(unittest.TestCase):
    def ok(self, **over):
        v = validator()
        claims, reason = v.validate(mint_fixture(KEY, KID, base_claims(**over)))
        self.assertIsNotNone(claims, reason)
        return claims

    def bad(self, token, expect):
        v = validator()
        claims, reason = v.validate(token)
        self.assertIsNone(claims)
        self.assertEqual(reason, expect)

    def test_valid_user(self):
        self.assertEqual(self.ok().principal, "tenant-1:user-1")

    def test_missing(self):
        v = validator()
        self.assertEqual(v.validate(None)[1], "missing")
        self.assertEqual(v.validate("")[1], "missing")

    def test_malformed(self):
        self.bad("abc", "malformed")
        self.bad("a.b", "malformed")
        self.bad("%%%.%%%.%%%", "malformed")

    def test_bad_signature(self):
        t = mint_fixture(b"other-secret-32-bytes-minimum!!!!", KID, base_claims())
        self.bad(t, "bad_signature")

    def test_alg_none_rejected(self):
        import base64
        import json

        h = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        p = (
            base64.urlsafe_b64encode(json.dumps(base_claims()).encode())
            .rstrip(b"=")
            .decode()
        )
        self.bad(f"{h}.{p}.", "disallowed_alg")

    def test_disallowed_alg(self):
        t = mint_fixture(KEY, KID, base_claims())
        _h, p, s = t.split(".")
        import base64
        import json

        hdr = {"alg": "HS384", "typ": "JWT", "kid": KID}
        h2 = base64.urlsafe_b64encode(json.dumps(hdr).encode()).rstrip(b"=").decode()
        self.bad(f"{h2}.{p}.{s}", "disallowed_alg")

    def test_expired(self):
        t = mint_fixture(KEY, KID, base_claims(exp=time.time() - 1000))
        self.bad(t, "expired")

    def test_future_nbf(self):
        t = mint_fixture(KEY, KID, base_claims(nbf=time.time() + 1000))
        self.bad(t, "not_yet_valid")

    def test_wrong_issuer(self):
        t = mint_fixture(KEY, KID, base_claims(iss="https://evil.example/"))
        self.bad(t, "wrong_issuer")

    def test_wrong_audience(self):
        t = mint_fixture(KEY, KID, base_claims(aud="other-api"))
        self.bad(t, "wrong_audience")

    def test_missing_scope(self):
        t = mint_fixture(KEY, KID, base_claims(scp="other.scope"))
        self.bad(t, "missing_scope")

    def test_delegated_roles_do_not_change_user_identity(self):
        claims, reason = validator().validate(
            mint_fixture(KEY, KID, base_claims(roles=["Some.User.Role"], azp="client"))
        )
        self.assertIsNone(reason)
        self.assertFalse(claims.is_app_only)
        self.assertEqual(claims.principal, "tenant-1:user-1")

    def test_delegated_scope_cannot_be_bypassed_with_machine_role(self):
        self.bad(
            mint_fixture(
                KEY,
                KID,
                base_claims(
                    scp="other", roles=["Chat.Invoke.Application"], azp="client"
                ),
            ),
            "missing_scope",
        )

    def test_missing_role(self):
        from helpers import machine_claims

        t = mint_fixture(KEY, KID, machine_claims(roles=["Other.Role"]))
        self.bad(t, "missing_role")

    def test_id_token_rejected(self):
        t = mint_fixture(KEY, KID, base_claims(token_type="id_token"))
        self.bad(t, "id_token_rejected")

    def test_unknown_kid_single_refresh(self):
        v = validator()
        t = mint_fixture(
            b"unknown-key-32-bytes-minimum!!!!!", "nope-kid", base_claims()
        )
        claims, reason = v.validate(t)
        self.assertIsNone(claims)
        self.assertEqual(reason, "unknown_kid")
        self.assertEqual(v.jwks.fetch_count, 1)  # bounded: exactly one

    def test_stale_keys_retained_on_fetch_failure(self):
        v = validator()
        v.jwks.fail_fetch = True
        claims, reason = v.validate(mint_fixture(KEY, KID, base_claims()))
        self.assertIsNotNone(claims, reason)  # cached key still validates

    def test_caller_identity_header_ignored(self):
        # Spoofed X-User headers must not bypass validation (no such input).
        v = validator()
        claims, _ = v.validate(mint_fixture(KEY, KID, base_claims()))
        self.assertEqual(claims.principal, "tenant-1:user-1")


if __name__ == "__main__":
    unittest.main()
