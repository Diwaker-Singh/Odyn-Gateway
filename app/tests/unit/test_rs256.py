"""M2: RS256 (PyJWT) negative matrix + HS256 test-only guard.

Keypair generated in-test via `cryptography` (no fixtures on disk, no
network). Mirrors the HS256 matrix through the SHARED claim rules, plus
algorithm-confusion (HS256 token into RS256 validator) and the live-config
HS256 refusal proof.
"""

import time
import unittest

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from helpers import (
    AUDIENCE,
    ISSUER,
    KEY,
    KID,
    base_claims,
    machine_claims,
    make_gateway,
)

from gateway.auth import Rs256Jwks, Rs256Validator

OTHER_KID = "other-rsa-kid"


def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


PRIV, PUB = keypair()
PRIV2, _ = keypair()


def jwks(pub=PUB, kid=KID):
    return Rs256Jwks({kid: pub})


def mint(key, kid, claims, alg="RS256"):
    return pyjwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def validator(**kw):
    args = dict(issuer=ISSUER, audience=AUDIENCE, jwks=jwks())
    args.update(kw)
    return Rs256Validator(**args)


class Rs256Tests(unittest.TestCase):
    def test_valid_user(self):
        v = validator()
        claims, reason = v.validate(mint(PRIV, KID, base_claims()))
        self.assertIsNotNone(claims, reason)
        self.assertEqual(claims.principal, "tenant-1:user-1")
        self.assertFalse(claims.is_app_only)

    def test_valid_machine(self):
        v = validator()
        claims, reason = v.validate(mint(PRIV, KID, machine_claims()))
        self.assertIsNotNone(claims, reason)
        self.assertEqual(claims.principal, "tenant-1:machine-app-1")
        self.assertTrue(claims.is_app_only)

    def test_bad_signature(self):
        v = validator()
        c, r = v.validate(mint(PRIV2, KID, base_claims()))
        self.assertIsNone(c)
        self.assertEqual(r, "bad_signature")

    def test_alg_none_rejected(self):
        import base64
        import json

        def b64(o):
            return (
                base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
            )

        forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(base_claims())}."
        v = validator()
        c, r = v.validate(forged)
        self.assertIsNone(c)
        self.assertEqual(r, "disallowed_alg")

    def test_hs256_confusion_rejected(self):
        """HS256-signed token must never validate on the RS256 path."""
        from gateway.auth import mint_fixture

        v = validator()
        c, r = v.validate(mint_fixture(KEY, KID, base_claims()))
        self.assertIsNone(c)
        self.assertEqual(r, "disallowed_alg")

    def test_expired(self):
        v = validator()
        c, r = v.validate(mint(PRIV, KID, base_claims(exp=time.time() - 1000)))
        self.assertIsNone(c)
        self.assertEqual(r, "expired")

    def test_future_nbf(self):
        v = validator()
        c, r = v.validate(mint(PRIV, KID, base_claims(nbf=time.time() + 1000)))
        self.assertIsNone(c)
        self.assertEqual(r, "not_yet_valid")

    def test_wrong_issuer_audience(self):
        v = validator()
        c, r = v.validate(mint(PRIV, KID, base_claims(iss="https://evil.example/")))
        self.assertIsNone(c)
        self.assertEqual(r, "wrong_issuer")
        c, r = v.validate(mint(PRIV, KID, base_claims(aud="other-api")))
        self.assertIsNone(c)
        self.assertEqual(r, "wrong_audience")

    def test_scope_role_id_token(self):
        v = validator()
        c, r = v.validate(mint(PRIV, KID, base_claims(scp="other")))
        self.assertEqual((c, r), (None, "missing_scope"))
        c, r = v.validate(mint(PRIV, KID, machine_claims(roles=["X"])))
        self.assertEqual((c, r), (None, "missing_role"))
        c, r = v.validate(mint(PRIV, KID, base_claims(token_type="id_token")))
        self.assertEqual((c, r), (None, "id_token_rejected"))

    def test_unknown_kid_single_refresh(self):
        seen = []
        v = validator(jwks=Rs256Jwks({KID: PUB}, refresh_hook=lambda: seen.append(1)))
        c, r = v.validate(mint(PRIV, OTHER_KID, base_claims()))
        self.assertIsNone(c)
        self.assertEqual(r, "unknown_kid")
        self.assertEqual(v.jwks.fetch_count, 1)
        self.assertEqual(seen, [1])

    def test_stale_keys_retained_on_fetch_failure(self):
        v = validator(jwks=Rs256Jwks({KID: PUB}, refresh_hook=None))
        v.jwks.fail_fetch = True
        c, r = v.validate(mint(PRIV, KID, base_claims()))
        self.assertIsNotNone(c, r)

    def test_serialization_round_trip(self):
        """Public key survives JWK export/import (documents JWKS wiring)."""
        from jwt.algorithms import RSAAlgorithm

        jwk_json = RSAAlgorithm.to_jwk(PUB, as_dict=False)
        back = RSAAlgorithm.from_jwk(jwk_json)
        v = validator(jwks=Rs256Jwks({KID: back}))
        c, r = v.validate(mint(PRIV, KID, base_claims()))
        self.assertIsNotNone(c, r)


class FixtureGuardTests(unittest.TestCase):
    def test_live_config_rejects_hs256(self):
        """HS256 fixtures are impossible outside stub/test configuration."""
        from gateway.auth import mint_fixture

        gw = make_gateway(upstream_mode="stub")
        # Simulate a live/prod configuration: fixture path disabled.
        gw.validator.test_hs256 = False
        from helpers import auth_headers, chat_body

        s, _, _ = gw.chat(
            auth_headers(mint_fixture(KEY, KID, base_claims())), chat_body()
        )
        self.assertEqual(s, 401)

    def test_fixture_validator_defaults_closed(self):
        from gateway.auth import JwtValidator

        v = JwtValidator(issuer=ISSUER, audience=AUDIENCE)
        self.assertFalse(v.test_hs256)


if __name__ == "__main__":
    unittest.main()
