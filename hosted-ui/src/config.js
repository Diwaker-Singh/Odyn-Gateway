export const CONFIG_KEY = "odyn.hosted-ui.connection.v1";

export function validateConnectionConfig(value) {
  if (!value || typeof value !== "object") throw new Error("Connection settings are missing.");
  for (const field of ["tenant_id", "client_id", "scope", "gateway_url"]) {
    if (typeof value[field] !== "string" || !value[field].trim()) {
      throw new Error(`Set ${field.replaceAll("_", " ")}.`);
    }
  }
  if (!/^[0-9a-f-]{36}$/i.test(value.tenant_id) || !/^[0-9a-f-]{36}$/i.test(value.client_id)) {
    throw new Error("Tenant ID and SPA client ID must be GUIDs.");
  }
  if (!/^api:\/\/[0-9a-f-]{36}\/chat\.invoke$/i.test(value.scope)) {
    throw new Error("API scope must use the api://<application-id>/chat.invoke format.");
  }
  let gateway;
  try {
    gateway = new URL(value.gateway_url);
  } catch {
    throw new Error("Gateway URL is invalid.");
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(gateway.hostname);
  if (
    (gateway.protocol !== "https:" && !(loopback && gateway.protocol === "http:")) ||
    gateway.username || gateway.password || gateway.search || gateway.hash ||
    gateway.pathname !== "/v1/chat"
  ) {
    throw new Error("Gateway URL must be HTTPS /v1/chat (HTTP is allowed only for loopback). ");
  }
  return {
    tenant_id: value.tenant_id.trim(),
    client_id: value.client_id.trim(),
    scope: value.scope.trim(),
    gateway_url: gateway.href,
  };
}

export async function loadConnectionConfig() {
  const response = await fetch(`${import.meta.env.BASE_URL}config.json`, { cache: "no-store" });
  if (!response.ok) throw new Error("Hosted UI configuration is unavailable.");
  const defaults = validateConnectionConfig(await response.json());
  try {
    const override = sessionStorage.getItem(CONFIG_KEY);
    return override ? validateConnectionConfig(JSON.parse(override)) : defaults;
  } catch (error) {
    sessionStorage.removeItem(CONFIG_KEY);
    throw error;
  }
}
