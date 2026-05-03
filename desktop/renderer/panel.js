/**
 * panel.js — Hoshino Agent Console
 *
 * Features:
 *   - Safe DOM construction (no innerHTML for dynamic content) +
 *     a small allow-list Markdown renderer for assistant replies.
 *   - Multi-session sidebar: create / switch / delete; chat history
 *     persists per session in localStorage.
 *   - Streaming chat via /api/chat/stream (SSE), with a thinking
 *     indicator and progressive reply rendering. Falls back to the
 *     non-streaming endpoint when the toggle is off.
 *   - Single-instance voice recognition: a second click stops it.
 *   - Readiness dot in the topbar reflects the latest /api/ready state.
 */

const BACKEND_URL = "http://127.0.0.1:8787";
const DEFAULT_SESSION_ID = "desktop-session";
const SESSIONS_INDEX_KEY = "hoshino.panel.sessions.v2";
const HISTORY_KEY_PREFIX = "hoshino.panel.history.";
const HISTORY_LIMIT = 80;

const chatLog = document.getElementById("chatLog");
const chatTitle = document.getElementById("chatTitle");
const capabilitiesEl = document.getElementById("capabilities");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message");
const imageInput = document.getElementById("imageInput");
const voiceBtn = document.getElementById("voiceBtn");
const taskContent = document.getElementById("taskContent");
const taskStatus = document.getElementById("taskStatus");
const timelineEl = document.getElementById("timeline");
const artifactsEl = document.getElementById("artifacts");
const sessionListEl = document.getElementById("sessionList");
const newSessionBtn = document.getElementById("newSessionBtn");
const streamingToggle = document.getElementById("streamingToggle");
const readyDot = document.getElementById("readyDot");
const settingsBtn = document.getElementById("settingsBtn");
const settingsBackdrop = document.getElementById("settingsBackdrop");
const settingsClose = document.getElementById("settingsClose");
const settingsTabs = document.getElementById("settingsTabs");
const settingsBody = document.getElementById("settingsBody");
const settingsRefresh = document.getElementById("settingsRefresh");
const settingsSave = document.getElementById("settingsSave");
const settingsStatus = document.getElementById("settingsStatus");

// ---------------------------------------------------------------------------
// Safe DOM helpers
// ---------------------------------------------------------------------------

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.title) node.title = options.title;
  if (options.attrs) {
    for (const [name, value] of Object.entries(options.attrs)) {
      node.setAttribute(name, value);
    }
  }
  for (const child of children) {
    if (child == null) continue;
    node.appendChild(child);
  }
  return node;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Minimal, safe Markdown renderer: escape first, then apply transforms.
function renderMarkdown(input) {
  const escaped = escapeHtml(input ?? "");

  const codeBlocks = [];
  let intermediate = escaped.replace(/```([\s\S]*?)```/g, (_, body) => {
    const idx = codeBlocks.length;
    codeBlocks.push(body);
    return ` CODE${idx} `;
  });

  const inlineCodes = [];
  intermediate = intermediate.replace(/`([^`\n]+)`/g, (_, body) => {
    const idx = inlineCodes.length;
    inlineCodes.push(body);
    return ` ICODE${idx} `;
  });

  intermediate = intermediate.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  intermediate = intermediate.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");

  intermediate = intermediate.replace(
    /\[([^\]\n]+)\]\(([^()\s]+)\)/g,
    (match, label, url) => {
      const safe = /^(https?:\/\/|\/|#)/i.test(url);
      if (!safe) return match;
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
  );

  intermediate = intermediate.replace(/ ICODE(\d+) /g, (_, idx) => {
    return `<code>${inlineCodes[Number(idx)]}</code>`;
  });
  intermediate = intermediate.replace(/ CODE(\d+) /g, (_, idx) => {
    return `<pre><code>${codeBlocks[Number(idx)]}</code></pre>`;
  });

  return intermediate.replace(/\n/g, "<br>");
}

// ---------------------------------------------------------------------------
// Session storage
// ---------------------------------------------------------------------------

function loadSessions() {
  try {
    const raw = localStorage.getItem(SESSIONS_INDEX_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch {
    /* fall through */
  }
  // Bootstrap a default session for first-time users.
  return [{ id: DEFAULT_SESSION_ID, title: "默认会话", lastUsed: Date.now() }];
}

function saveSessions(sessions) {
  try {
    localStorage.setItem(SESSIONS_INDEX_KEY, JSON.stringify(sessions));
  } catch {
    /* quota exceeded — drop silently */
  }
}

function historyKey(sessionId) {
  return `${HISTORY_KEY_PREFIX}${sessionId}`;
}

function loadHistory(sessionId) {
  try {
    const raw = localStorage.getItem(historyKey(sessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(sessionId, history) {
  try {
    const trimmed = history.slice(-HISTORY_LIMIT);
    localStorage.setItem(historyKey(sessionId), JSON.stringify(trimmed));
  } catch {
    /* drop silently */
  }
}

let sessions = loadSessions();
let activeSessionId = sessions[0].id;
let history = loadHistory(activeSessionId);

function activeSession() {
  return sessions.find((s) => s.id === activeSessionId) || sessions[0];
}

function recordHistory(role, text, meta = "") {
  history.push({ role, text, meta });
  saveHistory(activeSessionId, history);
  // Update session preview.
  const session = activeSession();
  if (session) {
    session.lastUsed = Date.now();
    session.preview = text.slice(0, 40);
    if (session.title === "默认会话" || session.title.startsWith("会话")) {
      // First user message becomes a friendly title.
      if (role === "user" && session.titleAuto !== false) {
        session.title = text.slice(0, 24) || session.title;
        session.titleAuto = true;
      }
    }
    saveSessions(sessions);
    renderSessions();
  }
}

// ---------------------------------------------------------------------------
// Chat rendering
// ---------------------------------------------------------------------------

function appendMessage(role, text, meta = "", { streaming = false } = {}) {
  const msg = el("div", { className: `msg ${role}${streaming ? " streaming" : ""}` });
  if (role === "assistant") {
    const body = el("div", { className: "msg-body" });
    body.innerHTML = renderMarkdown(text); // Safe: escape + whitelisted tags.
    msg.appendChild(body);
  } else {
    msg.textContent = text;
  }
  if (meta) {
    msg.appendChild(el("span", { className: "meta", text: meta }));
  }
  chatLog.appendChild(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return msg;
}

function appendThinkingIndicator() {
  const msg = el("div", { className: "msg thinking" }, [
    el("span"),
    el("span"),
    el("span"),
  ]);
  chatLog.appendChild(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return msg;
}

function updateStreamingMessage(node, fullText) {
  const body = node.querySelector(".msg-body");
  if (body) body.innerHTML = renderMarkdown(fullText);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function finalizeStreamingMessage(node, meta = "") {
  node.classList.remove("streaming");
  if (meta) {
    node.appendChild(el("span", { className: "meta", text: meta }));
  }
}

function reloadHistoryView() {
  clearChildren(chatLog);
  for (const item of history) {
    appendMessage(item.role, item.text, item.meta || "");
  }
}

// ---------------------------------------------------------------------------
// Session sidebar
// ---------------------------------------------------------------------------

function renderSessions() {
  clearChildren(sessionListEl);
  if (!sessions.length) {
    sessionListEl.appendChild(el("p", { className: "empty", text: "还没有会话。" }));
    return;
  }
  // Sort by lastUsed descending so the most recent is on top.
  const sorted = [...sessions].sort((a, b) => (b.lastUsed || 0) - (a.lastUsed || 0));
  for (const session of sorted) {
    const item = el("div", {
      className: `session-item${session.id === activeSessionId ? " active" : ""}`,
      attrs: { "data-id": session.id },
    });

    const titleEl = el("div", { className: "session-title", text: session.title || "未命名会话" });
    const previewEl = el("div", { className: "session-preview", text: session.preview || "尚未开始对话" });
    const deleteBtn = el("button", {
      className: "session-delete",
      title: "删除会话",
      text: "×",
      attrs: { type: "button", "aria-label": "删除会话" },
    });

    item.appendChild(titleEl);
    item.appendChild(previewEl);
    item.appendChild(deleteBtn);

    item.addEventListener("click", (evt) => {
      if (evt.target === deleteBtn) return;
      switchSession(session.id);
    });
    deleteBtn.addEventListener("click", (evt) => {
      evt.stopPropagation();
      deleteSession(session.id);
    });

    sessionListEl.appendChild(item);
  }

  chatTitle.textContent = activeSession()?.title || "对话";
}

function switchSession(id) {
  if (id === activeSessionId) return;
  activeSessionId = id;
  history = loadHistory(activeSessionId);
  reloadHistoryView();
  taskStatus.textContent = "Idle";
  taskStatus.classList.remove("streaming");
  renderSessions();
  // Side panels reset — they're ephemeral and tied to the previous turn.
  renderTask({});
  renderTimeline([]);
  renderArtifacts([]);
}

function createSession() {
  const id = `session-${Date.now().toString(36)}`;
  const newSession = {
    id,
    title: `会话 ${sessions.length + 1}`,
    titleAuto: false,
    lastUsed: Date.now(),
    preview: "",
  };
  sessions.push(newSession);
  saveSessions(sessions);
  switchSession(id);
}

function deleteSession(id) {
  // Block deleting the last remaining session — there must always be one.
  if (sessions.length <= 1) {
    return;
  }
  if (!confirm("确认删除该会话及其历史？")) return;
  sessions = sessions.filter((s) => s.id !== id);
  try {
    localStorage.removeItem(historyKey(id));
  } catch {
    /* ignore */
  }
  saveSessions(sessions);
  if (activeSessionId === id) {
    activeSessionId = sessions[0].id;
    history = loadHistory(activeSessionId);
    reloadHistoryView();
  }
  renderSessions();
}

newSessionBtn.addEventListener("click", () => createSession());

// ---------------------------------------------------------------------------
// Side panels (task / timeline / artifacts)
// ---------------------------------------------------------------------------

function summarizeTools(toolCalls) {
  if (!toolCalls.length) return "none";
  return toolCalls.map((item) => item.name).join(", ");
}

function renderTask(task) {
  taskStatus.textContent = task.status || "completed";
  clearChildren(taskContent);

  if (!task.title && !task.steps) {
    taskContent.appendChild(el("p", { className: "empty", text: "等待你的第一个任务。" }));
    return;
  }

  taskContent.appendChild(el("p", { className: "task-title", text: task.title || "Untitled task" }));
  taskContent.appendChild(
    el("p", {
      className: "meta-text",
      text: `${task.owner || "unknown-agent"} · ${task.step_count || 0} steps`,
    })
  );

  const meta = el("div", { className: "task-meta" }, [
    el("span", { className: "chip", text: task.owner || "agent" }),
    el("span", { className: "chip", text: task.status || "completed" }),
  ]);
  taskContent.appendChild(meta);

  const steps = el("div", { className: "task-steps" });
  for (const step of task.steps || []) {
    const head = el("div", { className: "step-head" }, [
      el("strong", { text: step.title || "" }),
      el("span", {
        className: `step-status ${step.status === "failed" ? "failed" : ""}`,
        text: step.status || "",
      }),
    ]);
    steps.appendChild(
      el("article", { className: "step" }, [
        head,
        el("p", { className: "step-detail", text: step.detail || "" }),
      ])
    );
  }
  taskContent.appendChild(steps);
}

function renderTimeline(toolCalls) {
  clearChildren(timelineEl);
  if (!toolCalls.length) {
    timelineEl.appendChild(el("p", { className: "empty", text: "工具调用会显示在这里。" }));
    return;
  }
  toolCalls.forEach((item, index) => {
    const head = el("div", { className: "timeline-head" }, [
      el("strong", { text: `${index + 1}. ${item.name}` }),
      el("span", {
        className: `chip${item.success === false ? " failed" : ""}`,
        text: item.success === false ? "failed" : "tool",
      }),
    ]);
    timelineEl.appendChild(
      el("article", { className: "timeline-item" }, [
        head,
        el("p", { className: "timeline-detail", text: item.result || "" }),
      ])
    );
  });
}

function renderArtifacts(artifacts) {
  clearChildren(artifactsEl);
  if (!artifacts.length) {
    artifactsEl.appendChild(el("p", { className: "empty", text: "截图和产物会显示在这里。" }));
    return;
  }
  for (const item of artifacts) {
    const url = item.url || "";
    const safeUrl = /^(https?:\/\/|\/)/i.test(url) ? url : "";
    const src = safeUrl.startsWith("http") ? safeUrl : `${BACKEND_URL}${safeUrl}`;
    const article = el("article", { className: "artifact-item" }, [
      el("strong", { text: item.label || "" }),
      el("p", { className: "meta-text", text: item.type || "" }),
    ]);
    if (src) {
      article.appendChild(el("img", { attrs: { src, alt: item.label || "artifact" } }));
    }
    artifactsEl.appendChild(article);
  }
}

// ---------------------------------------------------------------------------
// Capabilities + ready dot
// ---------------------------------------------------------------------------

function renderCapabilities(capabilities) {
  clearChildren(capabilitiesEl);

  const runtime = el("div", { className: "status-group" }, [
    el("span", { className: "status-label", text: "Runtime" }),
    el("span", { className: "chip", text: capabilities.provider || "" }),
    el("span", { className: "chip", text: capabilities.model || "" }),
    el("span", { className: "chip", text: `vision:${capabilities.vision_provider || "?"}` }),
    el("span", { className: "chip", text: `embed:${capabilities.embedding_provider || "?"}` }),
  ]);

  const features = capabilities.features || {};
  const featureRow = el("div", { className: "status-group" }, [
    el("span", { className: "status-label", text: "Features" }),
    el("span", { className: "chip", text: `vision:${features.vision ? "on" : "off"}` }),
    el("span", { className: "chip", text: `tts:${features.tts_engine || "browser-speech"}` }),
    el("span", { className: "chip", text: `memory:${features.semantic_memory ? "on" : "off"}` }),
  ]);

  const tools = el("div", { className: "status-group" }, [
    el("span", { className: "status-label", text: "Tools" }),
    ...(capabilities.tools || []).slice(0, 8).map((name) =>
      el("span", { className: "chip", text: String(name) })
    ),
  ]);

  capabilitiesEl.appendChild(runtime);
  capabilitiesEl.appendChild(featureRow);
  capabilitiesEl.appendChild(tools);
}

async function refreshReady() {
  try {
    const resp = await fetch(`${BACKEND_URL}/api/ready`);
    if (!resp.ok) {
      readyDot.className = "ready-dot error";
      readyDot.title = `Backend unhealthy (HTTP ${resp.status})`;
      return;
    }
    const data = await resp.json();
    const visionOk = data?.model?.vision_supported;
    const memoryOk = !data?.memory?.semantic_enabled || data?.memory?.semantic_available;
    const fullyHealthy = visionOk && memoryOk;
    readyDot.className = `ready-dot ${fullyHealthy ? "ok" : "degraded"}`;
    readyDot.title = `Provider: ${data.model.provider} / ${data.model.name}\n` +
      `Vision: ${visionOk ? "on" : "off"}\n` +
      `Memory: ${memoryOk ? "ready" : "unavailable"}\n` +
      `Tools: ${data.tools.count}, MCP running: ${data.mcp.running}/${data.mcp.registered}`;
  } catch (err) {
    readyDot.className = "ready-dot error";
    readyDot.title = `Backend unreachable: ${err && err.message ? err.message : err}`;
  }
}

async function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function loadCapabilities() {
  const capabilities = await window.bishoujo.capabilities();
  renderCapabilities(capabilities);
}

// ---------------------------------------------------------------------------
// SSE consumer
// ---------------------------------------------------------------------------

async function streamChat(payload, { onEvent, signal } = {}) {
  const resp = await fetch(`${BACKEND_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`Stream failed: HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const eventLine = raw.match(/^event:\s*(\S+)/m);
      const dataLine = raw.match(/^data:\s*(.*)$/m);
      if (!eventLine) continue;
      let data = null;
      if (dataLine) {
        try {
          data = JSON.parse(dataLine[1]);
        } catch {
          data = dataLine[1];
        }
      }
      onEvent?.({ event: eventLine[1], data });
    }
  }
}

// ---------------------------------------------------------------------------
// Composer submit — streaming or one-shot depending on toggle
// ---------------------------------------------------------------------------

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  appendMessage("user", text);
  recordHistory("user", text);
  taskStatus.textContent = "running";
  taskStatus.classList.add("streaming");
  voiceBtn.disabled = true;
  composer.querySelector("button[type='submit']").disabled = true;

  const attachments = [];
  const file = imageInput.files?.[0];
  if (file) {
    attachments.push({
      kind: "image",
      mime_type: file.type,
      data_url: await fileToDataUrl(file),
    });
  }

  const payload = { message: text, session_id: activeSessionId, attachments };

  try {
    if (streamingToggle.checked) {
      await runStreaming(payload);
    } else {
      await runOneShot(payload);
    }
    messageInput.value = "";
    imageInput.value = "";
  } catch (error) {
    taskStatus.textContent = "failed";
    taskStatus.classList.remove("streaming");
    const reason = error && error.message ? error.message : String(error);
    appendMessage("assistant", `请求失败：${reason}`);
    recordHistory("assistant", `请求失败：${reason}`);
  } finally {
    voiceBtn.disabled = false;
    composer.querySelector("button[type='submit']").disabled = false;
    taskStatus.classList.remove("streaming");
  }
});

async function runOneShot(payload) {
  const thinking = appendThinkingIndicator();
  let response;
  try {
    response = await window.bishoujo.chat(payload);
  } finally {
    thinking.remove();
  }

  const meta = `${response.trace.active_agent} | tools: ${summarizeTools(response.trace.tool_calls)}`;
  appendMessage("assistant", response.reply, meta);
  recordHistory("assistant", response.reply, meta);

  renderTask(response.task || {});
  renderTimeline(response.trace.tool_calls || []);
  renderArtifacts(response.artifacts || []);
}

async function runStreaming(payload) {
  const thinking = appendThinkingIndicator();
  let assistantNode = null;
  let assistantText = "";
  let toolCalls = [];
  let activeAgent = "";

  await streamChat(payload, {
    onEvent: ({ event, data }) => {
      if (event === "intent") {
        activeAgent = data?.delegated_to || activeAgent;
      } else if (event === "tool_call") {
        toolCalls.push(data);
        renderTimeline(toolCalls);
      } else if (event === "delta") {
        if (!assistantNode) {
          thinking.remove();
          assistantNode = appendMessage("assistant", "", "", { streaming: true });
        }
        assistantText += data?.text || "";
        updateStreamingMessage(assistantNode, assistantText);
      } else if (event === "done") {
        if (!assistantNode) {
          thinking.remove();
          assistantNode = appendMessage("assistant", data?.reply || "", "", { streaming: true });
          assistantText = data?.reply || "";
        } else if (data?.reply && data.reply !== assistantText) {
          assistantText = data.reply;
          updateStreamingMessage(assistantNode, assistantText);
        }
        const meta = `${activeAgent || data?.trace?.active_agent || "agent"} | tools: ${summarizeTools(data?.trace?.tool_calls || [])}`;
        finalizeStreamingMessage(assistantNode, meta);
        recordHistory("assistant", assistantText, meta);
        renderTask(data?.task || {});
        renderTimeline(data?.trace?.tool_calls || toolCalls);
        renderArtifacts(data?.artifacts || []);
      } else if (event === "error") {
        if (assistantNode) {
          finalizeStreamingMessage(assistantNode, "");
        } else {
          thinking.remove();
        }
        const message = data?.message || "服务器错误";
        appendMessage("assistant", `请求失败：${message}`);
        recordHistory("assistant", `请求失败：${message}`);
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Voice recognition — single instance, click again to stop
// ---------------------------------------------------------------------------

let activeRecognition = null;

function stopRecognition() {
  if (activeRecognition) {
    try { activeRecognition.stop(); } catch { /* ignore */ }
    activeRecognition = null;
    voiceBtn.textContent = "语音输入";
    voiceBtn.classList.remove("recording");
  }
}

voiceBtn.addEventListener("click", () => {
  if (activeRecognition) {
    stopRecognition();
    return;
  }
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    appendMessage("assistant", "当前环境不支持 Web Speech API，可继续使用文本输入。");
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    messageInput.value = transcript;
    appendMessage("assistant", `已转写语音：${transcript}`);
  };
  recognition.onend = () => stopRecognition();
  recognition.onerror = (event) => {
    appendMessage("assistant", `语音输入错误：${event.error || "unknown"}`);
    stopRecognition();
  };
  try {
    recognition.start();
    activeRecognition = recognition;
    voiceBtn.textContent = "停止录音";
    voiceBtn.classList.add("recording");
  } catch (error) {
    appendMessage("assistant", `语音输入启动失败：${error.message}`);
  }
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

renderSessions();
reloadHistoryView();
loadCapabilities().catch((error) => {
  appendMessage("assistant", `能力加载失败：${error.message}`);
});
refreshReady();
setInterval(refreshReady, 30_000);

// ===========================================================================
// Settings modal
// ===========================================================================

let settingsState = null;        // last-loaded backend snapshot
let providerListing = null;      // /api/settings/providers result
let pendingPatch = {};           // unsaved field deltas
let activeTab = "general";

function setSettingsStatus(text, kind = "") {
  if (!settingsStatus) return;
  settingsStatus.textContent = text || "";
  settingsStatus.className = `settings-status${kind ? " " + kind : ""}`;
}

function openSettings(focusTab) {
  if (focusTab) selectTab(focusTab);
  settingsBackdrop.hidden = false;
  // Lazy-load on first open + refresh whenever opened so the values match
  // any change made via the pet's right-click menu while it was closed.
  reloadSettings();
}

function closeSettings() {
  settingsBackdrop.hidden = true;
  pendingPatch = {};
  setSettingsStatus("");
}

function selectTab(name) {
  activeTab = name;
  settingsTabs.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  renderSettingsBody();
}

settingsBtn?.addEventListener("click", () => openSettings());
settingsClose?.addEventListener("click", () => closeSettings());
settingsBackdrop?.addEventListener("click", (event) => {
  if (event.target === settingsBackdrop) closeSettings();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !settingsBackdrop.hidden) closeSettings();
});
settingsTabs?.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-tab]");
  if (btn) selectTab(btn.dataset.tab);
});
settingsRefresh?.addEventListener("click", () => reloadSettings());
settingsSave?.addEventListener("click", () => saveSettings());

// The pet's right-click menu can deep-link into a specific tab.
window.bishoujo.onOpenSettings?.((tab) => openSettings(tab || "general"));

function describeError(error, action) {
  const raw = error?.message || String(error);
  // The Electron IPC layer wraps thrown errors with the channel name. When
  // the underlying cause is "fetch failed" the backend is unreachable;
  // surface that as a single sentence the user can act on, instead of the
  // raw "Error invoking remote method 'agent:settings:get': TypeError: fetch failed".
  if (/fetch failed/i.test(raw)) {
    return `${action}失败：无法连接 agent-core (127.0.0.1:8787)。请确认后端正在运行，然后点"重新加载"。`;
  }
  if (/HTTP\s*4\d\d/i.test(raw) || /failed:\s*4\d\d/.test(raw)) {
    return `${action}失败：请求被后端拒绝 — ${raw}`;
  }
  if (/HTTP\s*5\d\d/i.test(raw) || /failed:\s*5\d\d/.test(raw)) {
    return `${action}失败：后端内部错误 — ${raw}`;
  }
  return `${action}失败：${raw}`;
}

function renderConnectionError(message) {
  if (!settingsBody) return;
  clearChildren(settingsBody);
  const card = el("div", { className: "settings-error-card" }, [
    el("strong", { text: "后端不可达" }),
    el("p", { className: "field-hint", text: message }),
    el("p", { className: "field-hint", text: "提示：在 agent-core 目录运行 `python -m uvicorn app.main:app --port 8787`" }),
  ]);
  settingsBody.appendChild(card);
}

async function reloadSettings() {
  setSettingsStatus("正在加载设置...");
  try {
    const [agent, providers] = await Promise.all([
      window.bishoujo.agentSettings(),
      window.bishoujo.listProviders(),
    ]);
    settingsState = { ...agent };
    providerListing = providers;
    pendingPatch = {};
    renderSettingsBody();
    setSettingsStatus("已加载", "ok");
    setTimeout(() => setSettingsStatus(""), 1500);
  } catch (error) {
    const msg = describeError(error, "加载");
    setSettingsStatus(msg, "error");
    if (!settingsState) renderConnectionError(msg);
  }
}

async function saveSettings() {
  if (!Object.keys(pendingPatch).length) {
    setSettingsStatus("没有要保存的改动");
    return;
  }
  setSettingsStatus("正在保存...");
  try {
    const updated = await window.bishoujo.updateAgentSettings(pendingPatch);
    settingsState = { ...updated };
    pendingPatch = {};
    renderSettingsBody();
    refreshReady();
    setSettingsStatus("已保存", "ok");
    setTimeout(() => setSettingsStatus(""), 1500);
  } catch (error) {
    setSettingsStatus(describeError(error, "保存"), "error");
  }
}

function effectiveValue(key) {
  return key in pendingPatch ? pendingPatch[key] : settingsState?.[key];
}

function setPending(key, value) {
  if (settingsState && settingsState[key] === value) {
    delete pendingPatch[key];
  } else {
    pendingPatch[key] = value;
  }
  // Update the dirty indicator without re-rendering the whole tab —
  // re-rendering would steal focus from the input the user is editing.
  if (Object.keys(pendingPatch).length) {
    setSettingsStatus(`有 ${Object.keys(pendingPatch).length} 项未保存`, "dirty");
  } else {
    setSettingsStatus("");
  }
}

// ---- Field builders ------------------------------------------------------

function fieldGroup(label, hint, child) {
  const group = el("div", { className: "field" }, [
    el("label", { className: "field-label", text: label }),
    child,
  ]);
  if (hint) {
    group.appendChild(el("p", { className: "field-hint", text: hint }));
  }
  return group;
}

function toggleField(label, hint, key) {
  const wrapper = el("div", { className: "field field-toggle" });
  const labelEl = el("div", { className: "field-toggle-text" }, [
    el("span", { className: "field-label", text: label }),
    hint ? el("span", { className: "field-hint", text: hint }) : null,
  ]);
  const sw = el("label", { className: "switch" });
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!effectiveValue(key);
  input.addEventListener("change", () => setPending(key, input.checked));
  const slider = el("span", { className: "slider" });
  sw.appendChild(input);
  sw.appendChild(slider);
  wrapper.appendChild(labelEl);
  wrapper.appendChild(sw);
  return wrapper;
}

function selectField(label, hint, key, options) {
  const select = document.createElement("select");
  for (const opt of options) {
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    if (opt.disabled) o.disabled = true;
    select.appendChild(o);
  }
  select.value = String(effectiveValue(key) ?? "");
  select.addEventListener("change", () => setPending(key, select.value));
  return fieldGroup(label, hint, select);
}

function textField(label, hint, key, { type = "text", placeholder = "" } = {}) {
  const input = document.createElement("input");
  input.type = type;
  input.placeholder = placeholder;
  const value = effectiveValue(key);
  input.value = value == null ? "" : String(value);
  input.addEventListener("input", () => {
    let next = input.value;
    if (type === "number") next = next === "" ? null : Number(next);
    setPending(key, next);
  });
  return fieldGroup(label, hint, input);
}

function sliderField(label, hint, key, { min = 0, max = 1, step = 0.05 } = {}) {
  const wrapper = el("div", { className: "field field-slider" });
  const top = el("div", { className: "field-slider-top" }, [
    el("span", { className: "field-label", text: label }),
    el("span", { className: "field-value", text: String(effectiveValue(key) ?? "") }),
  ]);
  const input = document.createElement("input");
  input.type = "range";
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(effectiveValue(key) ?? min);
  const valueEl = top.querySelector(".field-value");
  input.addEventListener("input", () => {
    const next = Number(input.value);
    valueEl.textContent = next.toString();
    setPending(key, next);
  });
  wrapper.appendChild(top);
  wrapper.appendChild(input);
  if (hint) wrapper.appendChild(el("p", { className: "field-hint", text: hint }));
  return wrapper;
}

// ---- Tab content --------------------------------------------------------

function renderSettingsBody() {
  if (!settingsBody) return;
  clearChildren(settingsBody);
  if (!settingsState) {
    settingsBody.appendChild(el("p", { className: "empty", text: "还未加载到设置。" }));
    return;
  }
  if (activeTab === "general") renderGeneralTab();
  else if (activeTab === "voice") renderVoiceTab();
  else if (activeTab === "memory") renderMemoryTab();
  else renderAboutTab();
}

function providerOptions(filter = () => true) {
  if (!providerListing) return [{ value: "", label: "(provider 列表加载中)" }];
  return providerListing.providers.filter(filter).map((p) => ({
    value: p.id,
    label: `${p.display_name}${p.configured ? "" : "  (未配置 API key)"}`,
    disabled: !p.configured,
  }));
}

function renderGeneralTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "对话模型" }),
    selectField("当前 Provider", "切换后立即生效，所有对话/分析都走新 provider。", "provider", providerOptions()),
    textField("当前 model（覆盖 provider 默认值）", "留空则使用各 provider 默认模型。", "model"),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "视觉模型" }),
    selectField("视觉 Provider", "用于屏幕观察、图片理解。", "vision_provider", providerOptions((p) => p.supports_vision)),
    textField("视觉 model id", "例如 Qwen/Qwen3-VL-8B-Instruct。", "vision_model"),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "可靠性" }),
    toggleField("Anthropic Prompt Caching", "对屏幕观察等长 prompt 节省 token。", "enable_prompt_cache"),
    textField("最大重试次数", "5xx / 429 / 网络错误重试上限。", "model_max_retries", { type: "number" }),
    textField("重试退避基准 (秒)", "实际等待按指数增长。", "model_retry_backoff_seconds", { type: "number" }),
    textField("Anthropic max_tokens", "Anthropic 单轮回复 token 上限。", "anthropic_max_tokens", { type: "number" }),
  ]);
  settingsBody.appendChild(groups);
}

function renderVoiceTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "Edge Neural TTS（默认云端 TTS）" }),
    toggleField("启用 Edge TTS", "失败时自动回落到浏览器 Speech Synthesis。", "enable_edge_tts"),
    selectField(
      "Edge 音色",
      "微软 Edge 神经网络音色 ID。",
      "edge_tts_voice",
      [
        { value: "zh-CN-XiaoxiaoNeural", label: "中文 · 晓晓 (清亮女声)" },
        { value: "zh-CN-YunxiNeural", label: "中文 · 云希 (青年男声)" },
        { value: "zh-CN-YunyangNeural", label: "中文 · 云扬 (旁白男声)" },
        { value: "zh-CN-XiaoyiNeural", label: "中文 · 晓伊 (甜美少女)" },
        { value: "zh-CN-YunjianNeural", label: "中文 · 云健 (沉稳男声)" },
        { value: "zh-CN-XiaohanNeural", label: "中文 · 晓涵 (温柔女声)" },
        { value: "en-US-JennyNeural", label: "English · Jenny" },
        { value: "en-US-GuyNeural", label: "English · Guy" },
      ],
    ),
    textField("语速调整 (%)", "例如 +0% / +10% / -20%。", "edge_tts_rate"),
    textField("音调调整 (Hz)", "例如 +0Hz / +50Hz / -30Hz。", "edge_tts_pitch"),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "MiniMax Speech (可选)" }),
    toggleField("启用 MiniMax 语音", "需要 MINIMAX_API_KEY 有 speech 配额。", "enable_minimax_voice"),
    textField("MiniMax 音色 ID", "", "minimax_tts_voice_id"),
    sliderField("MiniMax 语速", "1.0 是常速。", "minimax_tts_speed", { min: 0.5, max: 2.0, step: 0.05 }),
    sliderField("MiniMax 音调", "0 是默认。", "minimax_tts_pitch", { min: -12, max: 12, step: 1 }),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "其它 TTS 引擎" }),
    toggleField("启用 ModelScope CosyVoice", "中文情感 TTS，音色更自然。", "enable_modelscope_tts"),
    toggleField("启用 Gemini TTS", "需要 GEMINI_TTS_API_KEY。", "enable_gemini_tts"),
    selectField(
      "Gemini 音色",
      "",
      "gemini_tts_voice",
      [
        { value: "Kore", label: "Kore" },
        { value: "Charon", label: "Charon" },
        { value: "Puck", label: "Puck" },
        { value: "Aoede", label: "Aoede" },
      ],
    ),
  ]);
  settingsBody.appendChild(groups);
}

function renderMemoryTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "语义记忆" }),
    toggleField("启用语义记忆", "用 chromadb 做语义召回。关闭后只用最近 N 轮摘要。", "enable_semantic_memory"),
    textField("召回 Top K", "每次召回多少条最相关历史。", "semantic_top_k", { type: "number" }),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "限流与清理" }),
    textField("限流容量 (token-bucket)", "0 = 关闭限流。", "rate_limit_capacity", { type: "number" }),
    textField("限流补充速率 (req/s)", "", "rate_limit_refill_per_second", { type: "number" }),
    textField("TTS 音频保留 (小时)", "0 = 永久保留；启动时清理过期文件。", "tts_audio_retention_hours", { type: "number" }),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "桌面自动化" }),
    toggleField("允许 GUI 自动化工具", "默认关闭，启用后 LLM 可以请求点击/按键。", "enable_gui_automation"),
  ]);
  settingsBody.appendChild(groups);
}

function renderAboutTab() {
  const lines = [
    `Provider: ${settingsState.provider} → ${settingsState.model}`,
    `Vision:   ${settingsState.vision_provider} → ${settingsState.vision_model}`,
    `Edge TTS: ${settingsState.enable_edge_tts ? "on" : "off"} (${settingsState.edge_tts_voice})`,
    `Memory:   semantic ${settingsState.enable_semantic_memory ? "on" : "off"} · top-k ${settingsState.semantic_top_k}`,
    `Rate limit: ${settingsState.rate_limit_capacity} tokens @ ${settingsState.rate_limit_refill_per_second}/s`,
  ];
  const pre = el("pre", { className: "about-pre", text: lines.join("\n") });
  const links = el("p", { className: "field-hint" }, [
    el("span", { text: "API keys 在 .env 中管理（API_KEY 不会通过本面板传输）。完整文档见 README。" }),
  ]);
  settingsBody.appendChild(pre);
  settingsBody.appendChild(links);
}
