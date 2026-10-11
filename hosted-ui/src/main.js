import { PublicClientApplication } from "@azure/msal-browser";
import { CONFIG_KEY, loadConnectionConfig, validateConnectionConfig } from "./config.js";
import { MAX_RESPONSE_TOKENS } from "./limits.js";
import "./style.css";

const $ = (selector) => document.querySelector(selector);
const status = $("#session-status");
const signIn = $("#sign-in");
const signOut = $("#sign-out");
const promptInput = $("#prompt");
const send = $("#send");
const form = $("#chat-form");
const conversation = $("#conversation");
const feedback = $("#feedback");
const welcome = $("#welcome");
const settingsForm = $("#settings-form");
const settings = $(".settings");
const history = [];
let config;
let auth;
let busy = false;

function showMessage(role, text) {
  welcome.classList.add("hidden");
  const bubble = document.createElement("div");
  bubble.className = `message ${role}`;
  bubble.textContent = text;
  conversation.append(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "end" });
}

function updateSession() {
  const loggedIn = Boolean(auth?.getActiveAccount());
  status.textContent = loggedIn ? "Signed in" : "Signed out";
  status.classList.toggle("online", loggedIn);
  signIn.classList.toggle("hidden", loggedIn);
  signOut.classList.toggle("hidden", !loggedIn);
  promptInput.disabled = !loggedIn || busy;
  send.disabled = !loggedIn || busy;
  promptInput.placeholder = loggedIn ? "Write a message…" : "Sign in to start chatting…";
}

function renderSettings() {
  $("#tenant-id").value = config.tenant_id;
  $("#client-id").value = config.client_id;
  $("#scope").value = config.scope;
  $("#gateway-url").value = config.gateway_url;
}

async function getAccessToken() {
  const account = auth.getActiveAccount();
  if (!account) throw new Error("Sign in before sending a message.");
  try {
    const result = await auth.acquireTokenSilent({ account, scopes: [config.scope] });
    return result.accessToken;
  } catch (error) {
    if (error?.name?.includes("InteractionRequired")) {
      await auth.acquireTokenRedirect({ account, scopes: [config.scope] });
      return null;
    }
    throw new Error("Could not refresh your sign-in. Sign out and sign in again.");
  }
}

async function start() {
  try {
    config = await loadConnectionConfig();
    renderSettings();
    const basePath = import.meta.env.BASE_URL;
    const redirectUri = new URL(basePath, window.location.origin).href;
    auth = new PublicClientApplication({
      auth: {
        clientId: config.client_id,
        authority: `https://login.microsoftonline.com/${config.tenant_id}`,
        redirectUri,
        postLogoutRedirectUri: redirectUri,
      },
      cache: { cacheLocation: "sessionStorage", storeAuthStateInCookie: false },
      system: { allowNativeBroker: false },
    });
    await auth.initialize();
    const result = await auth.handleRedirectPromise();
    if (result?.account) auth.setActiveAccount(result.account);
    if (!auth.getActiveAccount()) {
      const [account] = auth.getAllAccounts();
      if (account) auth.setActiveAccount(account);
    }
    updateSession();
    if (!result && !auth.getActiveAccount()) settings.open = true;
  } catch (error) {
    feedback.textContent = error.message || "The chat demo could not start.";
    settings.open = true;
    signIn.disabled = true;
  }
}

signIn.addEventListener("click", async () => {
  signIn.disabled = true;
  feedback.textContent = "Opening Microsoft sign-in…";
  try {
    await auth.loginRedirect({ scopes: [config.scope], prompt: "select_account" });
  } catch {
    feedback.textContent = "Microsoft sign-in could not start. Check the SPA redirect configuration.";
    signIn.disabled = false;
  }
});

signOut.addEventListener("click", async () => {
  const account = auth.getActiveAccount();
  history.length = 0;
  conversation.querySelectorAll(".message").forEach((node) => node.remove());
  welcome.classList.remove("hidden");
  await auth.logoutRedirect({ account });
});

settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const next = validateConnectionConfig({
      tenant_id: $("#tenant-id").value,
      client_id: $("#client-id").value,
      scope: $("#scope").value,
      gateway_url: $("#gateway-url").value,
    });
    sessionStorage.setItem(CONFIG_KEY, JSON.stringify(next));
    window.location.reload();
  } catch (error) {
    feedback.textContent = error.message;
  }
});

$("#reset-settings").addEventListener("click", () => {
  sessionStorage.removeItem(CONFIG_KEY);
  window.location.reload();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = promptInput.value.trim();
  if (!text || busy || !auth?.getActiveAccount()) return;
  const messages = [...history, { role: "user", content: text }].slice(-12);
  promptInput.value = "";
  showMessage("user", text);
  busy = true;
  updateSession();
  feedback.textContent = "Sending one bounded request through the gateway…";
  try {
    const token = await getAccessToken();
    if (!token) return;
    const response = await fetch(config.gateway_url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "default", messages, max_tokens: MAX_RESPONSE_TOKENS }),
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const requestId = payload?.error?.request_id;
      const message = response.status === 401
        ? "Your sign-in was rejected. Sign out and sign in again."
        : response.status === 429
          ? "The gateway rate limit was reached. Wait before trying again."
          : response.status >= 500
            ? "The gateway could not complete the request."
            : "The gateway rejected the request.";
      feedback.textContent = requestId ? `${message} Request ${requestId}.` : message;
      return;
    }
    const answer = payload?.data?.content;
    if (typeof answer !== "string") throw new Error("Gateway response was incomplete.");
    history.push({ role: "user", content: text }, { role: "assistant", content: answer });
    showMessage("assistant", answer);
    const requestId = payload.request_id || "completed";
    const usage = Number.isInteger(payload.data.prompt_tokens)
      ? ` · ${payload.data.prompt_tokens} input / ${payload.data.completion_tokens ?? 0} output tokens`
      : "";
    feedback.textContent = `Request ${requestId}${usage}`;
  } catch (error) {
    feedback.textContent = error.message?.startsWith("Could not refresh")
      ? error.message
      : "The request may have reached the gateway. Check status before retrying.";
  } finally {
    busy = false;
    updateSession();
  }
});

await start();
