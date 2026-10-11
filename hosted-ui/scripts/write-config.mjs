import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const required = [
  "ODYN_ENTRA_TENANT_ID",
  "ODYN_ENTRA_CLIENT_ID",
  "ODYN_API_SCOPE",
  "ODYN_GATEWAY_CHAT_URL",
];
const values = Object.fromEntries(required.map((name) => [name, process.env[name]?.trim()]));
for (const [name, value] of Object.entries(values)) {
  if (!value) throw new Error(`Missing public Pages variable ${name}`);
}
for (const name of ["ODYN_ENTRA_TENANT_ID", "ODYN_ENTRA_CLIENT_ID"]) {
  if (!/^[0-9a-f-]{36}$/i.test(values[name])) throw new Error(`${name} must be a GUID`);
}
if (!/^api:\/\/[0-9a-f-]{36}\/chat\.invoke$/i.test(values.ODYN_API_SCOPE)) {
  throw new Error("ODYN_API_SCOPE must be api://<api-app-id>/chat.invoke");
}
const gateway = new URL(values.ODYN_GATEWAY_CHAT_URL);
if (
  gateway.protocol !== "https:" || gateway.username || gateway.password ||
  gateway.search || gateway.hash || gateway.pathname !== "/v1/chat"
) {
  throw new Error("ODYN_GATEWAY_CHAT_URL must be an HTTPS /v1/chat URL");
}

const config = {
  tenant_id: values.ODYN_ENTRA_TENANT_ID,
  client_id: values.ODYN_ENTRA_CLIENT_ID,
  scope: values.ODYN_API_SCOPE,
  gateway_url: gateway.href,
};
const output = resolve("public/config.json");
await mkdir("public", { recursive: true });
await writeFile(output, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o644 });
console.log("Wrote public SPA configuration without secrets.");
