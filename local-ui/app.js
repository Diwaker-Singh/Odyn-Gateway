const signInButton = document.querySelector("#sign-in");
const signOutButton = document.querySelector("#sign-out");
const statusLabel = document.querySelector("#session-status");
const promptInput = document.querySelector("#prompt");
const sendButton = document.querySelector("#send");
const chatForm = document.querySelector("#chat-form");
const conversation = document.querySelector("#conversation");
const feedback = document.querySelector("#feedback");
const welcome = document.querySelector("#welcome");
const messages = [];

function setSignedIn(signedIn) {
  statusLabel.textContent = signedIn ? "Signed in" : "Signed out";
  statusLabel.classList.toggle("online", signedIn);
  signInButton.classList.toggle("hidden", signedIn);
  signOutButton.classList.toggle("hidden", !signedIn);
  promptInput.disabled = !signedIn;
  sendButton.disabled = !signedIn;
  promptInput.placeholder = signedIn ? "Write a message…" : "Sign in to start chatting…";
}

function addMessage(role, content) {
  welcome.classList.add("hidden");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  item.textContent = content;
  conversation.append(item);
  item.scrollIntoView({ behavior: "smooth", block: "end" });
}

async function refreshSession() {
  try {
    const response = await fetch("/api/session", { cache: "no-store" });
    const state = await response.json();
    setSignedIn(Boolean(state.authenticated));
  } catch {
    feedback.textContent = "Could not reach the local chat service.";
  }
}

signInButton.addEventListener("click", async () => {
  signInButton.disabled = true;
  feedback.textContent = "Opening Microsoft sign-in…";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok || !result.auth_uri) throw new Error(result.error || "Sign-in could not start");
    window.location.assign(result.auth_uri);
    // Some desktop browser setups route Microsoft sign-in to another profile
    // window instead of navigating this tab. If this document remains open,
    // restore the control and tell the user to continue in the callback window.
    window.setTimeout(() => {
      if (window.location.origin === "http://localhost:8400") {
        feedback.textContent = "If Microsoft sign-in opened in another Edge window, complete it there and use the Odyn Gateway page it returns to.";
        signInButton.disabled = false;
      }
    }, 4000);
  } catch (error) {
    feedback.textContent = error.message || "Sign-in could not start.";
    signInButton.disabled = false;
  }
});

signOutButton.addEventListener("click", async () => {
  await fetch("/api/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    cache: "no-store",
  });
  messages.length = 0;
  conversation.querySelectorAll(".message").forEach((node) => node.remove());
  welcome.classList.remove("hidden");
  feedback.textContent = "Signed out. The in-memory access token was cleared.";
  setSignedIn(false);
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = promptInput.value.trim();
  if (!content || sendButton.disabled) return;
  const outgoing = [...messages, { role: "user", content }].slice(-12);
  promptInput.value = "";
  addMessage("user", content);
  sendButton.disabled = true;
  promptInput.disabled = true;
  feedback.textContent = "Sending one request through the live gateway…";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: outgoing }),
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) {
      feedback.textContent = result.error || `Gateway returned HTTP ${response.status}.`;
      if (result.request_id) feedback.textContent += ` · Request ${result.request_id}`;
      if (response.status === 401) setSignedIn(false);
      return;
    }
    const answer = result.data.content;
    messages.push({ role: "user", content }, { role: "assistant", content: answer });
    addMessage("assistant", answer);
    const usage = result.data.prompt_tokens !== undefined
      ? ` · ${result.data.prompt_tokens} input / ${result.data.completion_tokens ?? 0} output tokens`
      : "";
    feedback.textContent = `Request ${result.request_id || "completed"}${usage}`;
  } catch {
    feedback.textContent = "The request may have reached the gateway. Check its status before sending again.";
  } finally {
    const state = await fetch("/api/session", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ authenticated: false }));
    setSignedIn(Boolean(state.authenticated));
    promptInput.focus();
  }
});

refreshSession();
