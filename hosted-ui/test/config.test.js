import test from "node:test";
import assert from "node:assert/strict";
import { validateConnectionConfig } from "../src/config.js";
import { MAX_RESPONSE_TOKENS } from "../src/limits.js";

const valid = {
  tenant_id: "11111111-1111-4111-8111-111111111111",
  client_id: "22222222-2222-4222-8222-222222222222",
  scope: "api://33333333-3333-4333-8333-333333333333/chat.invoke",
  gateway_url: "https://gateway.example/v1/chat",
};

test("demo UI uses a bounded but conversational response limit", () => {
  assert.equal(MAX_RESPONSE_TOKENS, 512);
});

test("accepts a tenant-scoped Entra client and HTTPS chat endpoint", () => {
  assert.deepEqual(validateConnectionConfig(valid), valid);
});

test("allows HTTP only for a loopback endpoint used in local development", () => {
  assert.equal(
    validateConnectionConfig({ ...valid, gateway_url: "http://localhost:8400/v1/chat" }).gateway_url,
    "http://localhost:8400/v1/chat",
  );
  assert.throws(
    () => validateConnectionConfig({ ...valid, gateway_url: "http://gateway.example/v1/chat" }),
    /HTTPS/,
  );
});

test("rejects credentials, query parameters, and unexpected API paths", () => {
  for (const gateway_url of [
    "https://user:pass@gateway.example/v1/chat",
    "https://gateway.example/v1/chat?debug=1",
    "https://gateway.example/other",
  ]) {
    assert.throws(() => validateConnectionConfig({ ...valid, gateway_url }));
  }
});

test("rejects wild URL scopes and invalid client identifiers", () => {
  assert.throws(() => validateConnectionConfig({ ...valid, scope: "https://attacker.example/.default" }));
  assert.throws(() => validateConnectionConfig({ ...valid, client_id: "not-a-guid" }));
});
