"""Schema/policy boundary tests (422/413/415/400 + ceiling rejection)."""

import json
import unittest

from gateway.config import GatewayConfig
from gateway.schema import validate_chat

CFG = GatewayConfig()


def body(**over) -> bytes:
    p = {
        "model": "default",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 10,
    }
    p.update(over)
    return json.dumps(p).encode()


class SchemaTests(unittest.TestCase):
    def test_valid(self):
        req, err = validate_chat("application/json", body(), CFG, "r1")
        self.assertIsNone(err)
        self.assertEqual(req.model_alias, "default")

    def test_wrong_content_type_415(self):
        _, err = validate_chat("text/plain", body(), CFG, "r1")
        self.assertEqual(err.status, 415)

    def test_oversize_413(self):
        big = (
            b'{"model": "default", "messages": [{"role": "user", "content": "'
            + b"x" * (CFG.max_body_bytes + 1)
            + b'"}]}'
        )
        _, err = validate_chat("application/json", big, CFG, "r1")
        self.assertEqual(err.status, 413)

    def test_cap_boundary_minus_one_ok(self):
        req, err = validate_chat(
            "application/json", body(max_tokens=CFG.max_tokens_ceiling - 1), CFG, "r1"
        )
        self.assertIsNone(err)
        assert req is not None
        self.assertEqual(req.max_tokens, CFG.max_tokens_ceiling - 1)

    def test_cap_boundary_ok(self):
        req, err = validate_chat(
            "application/json", body(max_tokens=CFG.max_tokens_ceiling), CFG, "r1"
        )
        self.assertIsNone(err)
        assert req is not None
        self.assertEqual(req.max_tokens, CFG.max_tokens_ceiling)

    def test_over_ceiling_rejected_not_clamped_422(self):
        _, err = validate_chat(
            "application/json", body(max_tokens=CFG.max_tokens_ceiling + 1), CFG, "r1"
        )
        self.assertEqual(err.status, 422)

    def test_unknown_model_422(self):
        _, err = validate_chat("application/json", body(model="gpt-99"), CFG, "r1")
        self.assertEqual(err.status, 422)

    def test_extra_fields_rejected_422(self):
        _, err = validate_chat(
            "application/json", body(prompt_injection="x"), CFG, "r1"
        )
        self.assertEqual(err.status, 422)

    def test_malformed_json_400(self):
        _, err = validate_chat("application/json", b"{nope", CFG, "r1")
        self.assertEqual(err.status, 400)

    def test_empty_messages_422(self):
        _, err = validate_chat("application/json", body(messages=[]), CFG, "r1")
        self.assertEqual(err.status, 422)

    def test_envelope_shape(self):
        _, err = validate_chat("text/plain", body(), CFG, "rid-9")
        env = err.envelope()
        self.assertEqual(set(env), {"error"})
        self.assertEqual(set(env["error"]), {"code", "message", "request_id"})
        self.assertEqual(env["error"]["request_id"], "rid-9")
        self.assertNotIn("Traceback", env["error"]["message"])


if __name__ == "__main__":
    unittest.main()
