"""Edge contract guards and shared fixtures; APIM execution needs cloud proof."""

import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from gateway.config import GatewayConfig
from gateway.schema import validate_chat

ROOT = Path(__file__).resolve().parents[3]
AZURE = ROOT / "infra/modules/llm-gateway/azure"


class EdgeValidationTests(unittest.TestCase):
    def policy(self):
        source = (AZURE / "policy.xml.tftpl").read_text()
        source = re.sub(r"%\{.*?\}", "", source)
        return ET.fromstring(re.sub(r"\$\{[^}]+\}", "fixture", source))

    def test_validation_is_chat_only_and_precedes_forwarding(self):
        tree = self.policy()
        inbound = tree.find("inbound")
        container = inbound.find(".//validate-content/..")
        self.assertIn(
            'context.Operation.Id == "post-chat"', container.attrib["condition"]
        )
        source = (AZURE / "policy.xml.tftpl").read_text()
        self.assertLess(source.index("<cors"), source.index("<validate-jwt"))
        self.assertLess(source.index("<validate-jwt"), source.index('id="body-size"'))
        self.assertLess(
            source.index('id="body-schema"'), source.index("<set-backend-service")
        )
        self.assertIsNotNone(tree.find("backend/forward-request"))

    def test_size_and_schema_use_prevention_and_shared_byte_limit(self):
        policies = self.policy().findall("inbound/.//validate-content")
        self.assertEqual(
            [p.attrib["id"] for p in policies], ["body-size", "body-schema"]
        )
        for policy in policies:
            self.assertEqual(policy.attrib["size-exceeded-action"], "prevent")
        schema_policy = policies[1]
        self.assertEqual(
            schema_policy.attrib["unspecified-content-type-action"], "prevent"
        )
        content = schema_policy.find("content")
        self.assertEqual(content.attrib["schema-id"], "chat-request-structure")
        self.assertEqual(content.attrib["action"], "prevent")
        self.assertEqual(content.attrib["validate-as"], "json")
        source = (AZURE / "policy.xml.tftpl").read_text()
        self.assertEqual(source.count('max-size="${max_body_bytes}"'), 2)
        self.assertIn(
            "max_body_bytes = var.max_body_bytes", (AZURE / "main.tf").read_text()
        )

    def test_media_type_accepts_charset_and_ignores_case(self):
        source = (AZURE / "policy.xml.tftpl").read_text()
        self.assertIn(".Split(';')[0].Trim().ToLowerInvariant()", source)
        self.assertIn('<set-status code="415"', source)

    def test_validation_errors_are_safe_client_errors(self):
        error = self.policy().find("on-error")
        status = error.find("set-status").attrib["code"]
        self.assertIn('PolicyId == "body-size" ? 413', status)
        self.assertIn('PolicyId == "body-schema" ? 400', status)
        body = error.find("set-body").text
        self.assertIn('"body_too_large"', body)
        self.assertIn('"bad_request"', body)
        self.assertNotIn("LastError.Message", body)
        self.assertNotIn("LastError.Details", body)
        self.assertNotIn("Request.Body", body)

    def test_schema_created_before_policy_with_document_object(self):
        source = (AZURE / "content-validation.tf").read_text()
        self.assertIn('type       = "Microsoft.ApiManagement/service/schemas"', source)
        self.assertIn("document    = jsondecode(file(", source)
        main = (AZURE / "main.tf").read_text()
        self.assertIn(
            "depends_on = [azurerm_resource_group_template_deployment.chat_schema]",
            main,
        )

    def test_shared_fixtures_preserve_application_validation(self):
        cases = json.loads(
            (ROOT / "app/tests/fixtures/chat-structure-cases.json").read_text()
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                _, error = validate_chat(
                    "application/json",
                    json.dumps(case["payload"]).encode(),
                    GatewayConfig(),
                    "fixture-request",
                )
                self.assertEqual(
                    error.status if error else None, case["application_status"]
                )

    def test_application_still_rejects_malformed_json_media_type_and_oversize(self):
        for media, body, status in (
            ("application/json", b"{invalid", 400),
            ("text/plain", b"{}", 415),
            ("application/json", b"x" * 32769, 413),
        ):
            with self.subTest(status=status):
                _, error = validate_chat(
                    media, body, GatewayConfig(), "fixture-request"
                )
                self.assertEqual(error.status, status)
