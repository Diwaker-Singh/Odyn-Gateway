"""Contract: shared envelope + header conformance across all routes.

This suite runs UNCHANGED against any adapter (local now, Azure later) given
a Gateway-compatible surface: every error AND success carries request_id,
every response carries X-Request-Id, 429 carries integer Retry-After.
"""

import unittest

from helpers import auth_headers, chat_body, make_gateway, user_token


class EnvelopeContractTests(unittest.TestCase):
    def check(self, status, body, headers):
        self.assertIn("X-Request-Id", headers)
        rid = headers["X-Request-Id"]
        if status == 200 and "data" in body:
            self.assertEqual(body.get("request_id"), rid)
        else:
            self.assertEqual(set(body), {"error"})
            self.assertEqual(set(body["error"]), {"code", "message", "request_id"})
            self.assertEqual(body["error"]["request_id"], rid)
        if status == 429:
            self.assertIn("Retry-After", headers)
            int(headers["Retry-After"])

    def test_contract_across_statuses(self):
        gw = make_gateway(
            per_principal_burst=1,
            per_principal_rps=0.01,
            global_burst=1000,
            global_rps=1000.0,
        )
        tok = user_token(gw)
        cases = [
            gw.chat(auth_headers(tok), chat_body()),  # 200
            gw.chat({"Content-Type": "application/json"}, chat_body()),  # 401
            gw.chat(auth_headers(user_token(gw, scp="x")), chat_body()),  # 403
            gw.chat(auth_headers(tok), b"{bad"),  # 400
            gw.chat(auth_headers(tok), chat_body()),  # 429 (burst 1)
        ]
        for s, b, h in cases:
            with self.subTest(status=s):
                self.check(s, b, h)


if __name__ == "__main__":
    unittest.main()
