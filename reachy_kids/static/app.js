const STATES = {
  starting: ["Starting…", ""],
  needs_setup: ["Needs an API key", "Add a key below to start talking."],
  connecting: ["Connecting…", ""],
  listening: ["Listening", "Go ahead and talk to Reachy."],
  hearing: ["Hearing you", ""],
  thinking: ["Thinking…", ""],
  speaking: ["Talking", ""],
  reconnecting: ["Reconnecting…", ""],
  stopped: ["Stopped", ""],
};

const form = document.getElementById("settings");
const voiceSelect = form.elements.voice;
let providers = [];
let formLoaded = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

function fillVoices(providerName, selected) {
  const provider = providers.find((p) => p.name === providerName);
  voiceSelect.replaceChildren(new Option("Default for mode", ""));
  for (const voice of provider ? provider.voices : []) {
    voiceSelect.add(new Option(voice[0].toUpperCase() + voice.slice(1), voice));
  }
  voiceSelect.value = provider && provider.voices.includes(selected) ? selected : "";
}

function syncVisibility() {
  const provider = form.elements.provider.value;
  for (const el of form.querySelectorAll("[data-provider]")) {
    el.hidden = el.dataset.provider !== provider;
  }
  for (const el of form.querySelectorAll(".kids-only")) {
    el.hidden = !form.elements.kids_mode.checked;
  }
}

function loadForm(settings) {
  form.elements.kids_mode.checked = settings.kids_mode;
  form.elements.provider.value = settings.provider;
  fillVoices(settings.provider, settings.voice);
  form.elements.child_name.value = settings.child_name;
  form.elements.child_age.value = settings.child_age ?? "";
  form.elements.language.value = settings.language;
  form.elements.keyterms.value = settings.keyterms.join(", ");
  for (const name of ["openai_api_key", "xai_api_key"]) {
    form.elements[name].value = "";
    form.elements[name].placeholder = settings[name] ? "Saved — leave blank to keep" : "Paste your key";
  }
  syncVisibility();
}

function renderTranscript(lines) {
  const list = document.getElementById("transcript");
  if (!lines.length) return;
  list.replaceChildren(
    ...lines.slice(-20).map(({ role, text }) => {
      const item = document.createElement("li");
      item.className = role;
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = role === "robot" ? "Reachy" : role === "child" ? "Kid" : "You";
      item.append(who, document.createTextNode(text));
      return item;
    }),
  );
  list.scrollTop = list.scrollHeight;
}

function renderState(state) {
  const [label, detail] = STATES[state.state] || [state.state, ""];
  document.getElementById("orb").dataset.state = state.state;
  document.getElementById("status-label").textContent = label;
  document.getElementById("status-detail").textContent = state.error || [state.provider, detail].filter(Boolean).join(" · ");
  renderTranscript(state.transcript);
  if (!formLoaded) {
    loadForm(state.settings);
    formLoaded = true;
  }
}

async function poll() {
  try {
    renderState(await api("/api/state"));
  } catch (error) {
    document.getElementById("status-label").textContent = "App not reachable";
    document.getElementById("orb").dataset.state = "stopped";
  }
  setTimeout(poll, 1000);
}

form.elements.provider.addEventListener("change", () => {
  fillVoices(form.elements.provider.value, "");
  syncVisibility();
});
form.elements.kids_mode.addEventListener("change", syncVisibility);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.getElementById("save-status");
  const age = form.elements.child_age.value;
  const changes = {
    provider: form.elements.provider.value,
    voice: form.elements.voice.value,
    kids_mode: form.elements.kids_mode.checked,
    child_name: form.elements.child_name.value.trim(),
    child_age: age ? Number(age) : null,
    language: form.elements.language.value.trim() || "en",
    keyterms: form.elements.keyterms.value.split(",").map((t) => t.trim()).filter(Boolean),
    openai_api_key: form.elements.openai_api_key.value.trim(),
    xai_api_key: form.elements.xai_api_key.value.trim(),
  };
  status.textContent = "Saving…";
  try {
    const state = await api("/api/settings", { method: "POST", body: JSON.stringify(changes) });
    loadForm(state.settings);
    renderState(state);
    status.textContent = "Saved. Reachy is reconnecting.";
  } catch (error) {
    status.textContent = `Could not save: ${error.message}`;
  }
});

document.getElementById("restart").addEventListener("click", () => api("/api/restart", { method: "POST" }));

api("/api/providers").then((list) => {
  providers = list;
  poll();
});
