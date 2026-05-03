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
