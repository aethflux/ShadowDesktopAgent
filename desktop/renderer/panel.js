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
const COMPANION_SESSION_ID = "pet-companion-session";
const COMPANION_SESSION_TITLE = "桌宠陪伴";
const COMPANION_HISTORY_RETENTION_MS = 5 * 60_000;
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

// Small safe Markdown renderer. It is intentionally limited, but covers the
// structures the agent commonly emits: headings, lists, blockquotes, tables,
// fenced code, inline code, bold/italic, and links. All user text is escaped
// before we introduce the small allow-list of HTML tags.
function renderInlineMarkdown(input) {
  const codeSpans = [];
  let text = String(input ?? "").replace(/`([^`\n]+)`/g, (_match, body) => {
    const idx = codeSpans.length;
    codeSpans.push(escapeHtml(body));
    return `\u0000CODE${idx}\u0000`;
  });

  text = escapeHtml(text);
  text = text.replace(
    /\[([^\]\n]+)\]\(([^()\s]+)\)/g,
    (match, label, url) => {
      const safe = /^(https?:\/\/|\/|#)/i.test(url);
      if (!safe) return match;
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
  );
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  text = text.replace(/\u0000CODE(\d+)\u0000/g, (_match, idx) => `<code>${codeSpans[Number(idx)]}</code>`);
  return text;
}

function splitTableRow(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isTableDivider(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function renderMarkdown(input) {
  const lines = String(input ?? "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let listType = null;
  let listItems = [];
  let blockquote = [];
  let inCode = false;
  let codeLines = [];
  let codeLang = "";

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!listType) return;
    html.push(`<${listType}>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${listType}>`);
    listType = null;
    listItems = [];
  };

  const flushBlockquote = () => {
    if (!blockquote.length) return;
    html.push(`<blockquote>${blockquote.map(renderInlineMarkdown).join("<br>")}</blockquote>`);
    blockquote = [];
  };

  const flushOpenBlocks = () => {
    flushParagraph();
    flushList();
    flushBlockquote();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = line.match(/^\s*```([\w-]*)\s*$/);
    if (fence) {
      if (inCode) {
        html.push(
          `<pre><code${codeLang ? ` class="language-${escapeHtml(codeLang)}"` : ""}>${escapeHtml(codeLines.join("\n"))}</code></pre>`
        );
        inCode = false;
        codeLines = [];
        codeLang = "";
      } else {
        flushOpenBlocks();
        inCode = true;
        codeLang = fence[1] || "";
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      flushOpenBlocks();
      continue;
    }

    const tableNext = lines[i + 1];
    if (line.includes("|") && tableNext && isTableDivider(tableNext)) {
      flushOpenBlocks();
      const headers = splitTableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      i -= 1;
      html.push(
        `<div class="md-table-wrap"><table><thead><tr>${headers
          .map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`)
          .join("")}</tr></thead><tbody>${rows
          .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`
      );
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushOpenBlocks();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      blockquote.push(quote[1]);
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      flushBlockquote();
      const nextType = ordered ? "ol" : "ul";
      if (listType && listType !== nextType) {
        flushList();
      }
      listType = nextType;
      listItems.push((unordered || ordered)[1]);
      continue;
    }

    flushList();
    flushBlockquote();
    paragraph.push(line);
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  flushOpenBlocks();
  return html.join("");
}

// ---------------------------------------------------------------------------
// Session storage
// ---------------------------------------------------------------------------

function companionSession() {
  return {
    id: COMPANION_SESSION_ID,
    title: COMPANION_SESSION_TITLE,
    fixed: true,
    titleAuto: false,
    lastUsed: Date.now(),
    preview: "保留最近 5 分钟桌宠输出",
  };
}

function normalizeSessions(items) {
  const byId = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || !item.id) continue;
    byId.set(item.id, item);
  }
  const companion = { ...companionSession(), ...(byId.get(COMPANION_SESSION_ID) || {}) };
  companion.title = COMPANION_SESSION_TITLE;
  companion.fixed = true;
  companion.titleAuto = false;
  byId.set(COMPANION_SESSION_ID, companion);

  if (!byId.has(DEFAULT_SESSION_ID)) {
    byId.set(DEFAULT_SESSION_ID, { id: DEFAULT_SESSION_ID, title: "默认会话", lastUsed: Date.now() });
  }
  return [...byId.values()];
}

function loadSessions() {
  try {
    const raw = localStorage.getItem(SESSIONS_INDEX_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed) && parsed.length) return normalizeSessions(parsed);
  } catch {
    /* fall through */
  }
  // Bootstrap a default session for first-time users.
  return normalizeSessions([{ id: DEFAULT_SESSION_ID, title: "默认会话", lastUsed: Date.now() }]);
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

function retentionForSession(sessionId) {
  return sessionId === COMPANION_SESSION_ID ? COMPANION_HISTORY_RETENTION_MS : 0;
}

function pruneHistory(sessionId, items) {
  const retention = retentionForSession(sessionId);
  if (!retention) return items;
  const cutoff = Date.now() - retention;
  return items.filter((item) => Number(item.ts || 0) >= cutoff);
}

function loadHistory(sessionId) {
  try {
    const raw = localStorage.getItem(historyKey(sessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? pruneHistory(sessionId, parsed) : [];
  } catch {
    return [];
  }
}

function saveHistory(sessionId, history) {
  try {
    const trimmed = pruneHistory(sessionId, history).slice(-HISTORY_LIMIT);
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

function ensureSession(sessionId, { title = null, fixed = false } = {}) {
  let session = sessions.find((s) => s.id === sessionId);
  if (!session) {
    session = {
      id: sessionId,
      title: title || `会话 ${sessions.length + 1}`,
      titleAuto: !title,
      fixed,
      lastUsed: Date.now(),
      preview: "",
    };
    sessions.push(session);
  }
  if (title) {
    session.title = title;
    session.titleAuto = false;
  }
  if (fixed) session.fixed = true;
  saveSessions(sessions);
  return session;
}

function recordHistoryForSession(sessionId, role, text, meta = "", options = {}) {
  const session = ensureSession(sessionId, options);
  const sessionHistory = loadHistory(sessionId);
  const id = options.id || `${sessionId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  if (sessionHistory.some((item) => item.id === id)) return;
  sessionHistory.push({ id, role, text, meta, ts: options.ts || Date.now(), source: options.source || "" });
  saveHistory(sessionId, sessionHistory);

  // Update session preview.
  if (session) {
    session.lastUsed = options.ts || Date.now();
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

  if (sessionId === activeSessionId) {
    history = loadHistory(activeSessionId);
    reloadHistoryView();
  }
}

function recordHistory(role, text, meta = "") {
  recordHistoryForSession(activeSessionId, role, text, meta);
}

function importPanelHistoryEntry(entry) {
  if (!entry || !entry.sessionId || !entry.text) return;
  const retention = Number(entry.retentionMs || retentionForSession(entry.sessionId) || 0);
  if (retention && Date.now() - Number(entry.ts || 0) > retention) return;
  recordHistoryForSession(
    entry.sessionId,
    entry.role === "user" ? "user" : "assistant",
    entry.text,
    entry.meta || "",
    {
      id: entry.id,
      ts: entry.ts || Date.now(),
      title: entry.title,
      fixed: !!entry.fixed,
      source: "pet",
    }
  );
}

async function syncPanelHistoryEvents() {
  try {
    const events = await window.bishoujo.listPanelHistoryEvents?.();
    for (const entry of events || []) {
      importPanelHistoryEntry(entry);
    }
  } catch (error) {
    console.warn("panel history sync failed", error);
  }
}

function pruneRollingHistories() {
  const before = history.length;
  const companionHistory = loadHistory(COMPANION_SESSION_ID);
  saveHistory(COMPANION_SESSION_ID, companionHistory);
  if (activeSessionId === COMPANION_SESSION_ID) {
    history = loadHistory(activeSessionId);
    if (history.length !== before) reloadHistoryView();
  }
  renderSessions();
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
    if (session.fixed) {
      deleteBtn.disabled = true;
      deleteBtn.title = "固定会话不能删除";
    }

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
  setTaskStatus("idle");
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
  if (sessions.find((s) => s.id === id)?.fixed) {
    return;
  }
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

function compactText(text, limit = 140) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function formatJson(value) {
  if (value == null) return "{}";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function inferDisplayStatus(item = {}) {
  const result = String(item.result || item.detail || "").toLowerCase();
  if (
    result.includes("blocked unsafe command") ||
    result.startsWith("tool ") && result.includes(" blocked:") ||
    result.includes("not allowlisted")
  ) {
    return "blocked";
  }
  if (
    item.success === false ||
    item.status === "failed" ||
    result.includes("failed:") ||
    result.startsWith("mcp tool error:") ||
    result.includes("access denied") ||
    result.includes("is not registered")
  ) {
    return "failed";
  }
  const exitMatches = [...result.matchAll(/\bexit=(\d+)\b/g)];
  if (exitMatches.some((match) => Number(match[1]) !== 0)) return "failed";
  return item.status || "completed";
}

function statusLabel(status) {
  if (status === "failed") return "失败";
  if (status === "blocked") return "已拦截";
  if (status === "running") return "运行中";
  if (status === "skipped") return "已跳过";
  if (status === "idle") return "Idle";
  return "完成";
}

function setTaskStatus(status) {
  const normalized = status || "completed";
  taskStatus.textContent = statusLabel(normalized);
  taskStatus.dataset.status = normalized;
  taskStatus.classList.toggle("streaming", normalized === "running");
}

function makeKv(label, value) {
  return el("div", { className: "kv" }, [
    el("span", { className: "kv-label", text: label }),
    el("strong", { className: "kv-value", text: value == null || value === "" ? "none" : String(value) }),
  ]);
}

function argsPreview(args = {}) {
  if (!args || !Object.keys(args).length) return "无参数";
  const preferred = ["command", "cwd", "path", "server_name", "session_id"];
  const parts = [];
  for (const key of preferred) {
    if (args[key] !== undefined) parts.push(`${key}=${JSON.stringify(args[key])}`);
  }
  for (const [key, value] of Object.entries(args)) {
    if (preferred.includes(key)) continue;
    if (parts.length >= 3) break;
    parts.push(`${key}=${JSON.stringify(value)}`);
  }
  return compactText(parts.join(" · "), 110);
}

function renderTask(task, trace = {}, memorySummary = "") {
  setTaskStatus(task.status || "completed");
  clearChildren(taskContent);

  if (!task.title && !task.steps) {
    taskContent.appendChild(el("p", { className: "empty", text: "等待你的第一个任务。" }));
    return;
  }

  const status = task.status || "completed";
  taskContent.appendChild(el("p", { className: "task-title", text: task.title || "Untitled task" }));
  taskContent.appendChild(el("p", { className: "meta-text", text: task.reply_preview ? `回复预览：${task.reply_preview}` : "" }));

  const overview = el("div", { className: "run-overview" }, [
    makeKv("状态", statusLabel(status)),
    makeKv("执行者", task.owner || trace.active_agent || "unknown-agent"),
    makeKv("步骤数", String(task.step_count || (task.steps || []).length || 0)),
    makeKv("工具数", String((trace.tool_calls || []).length)),
  ]);
  taskContent.appendChild(overview);

  if (trace.reasoning || trace.delegated_to || trace.active_agent) {
    const route = el("details", { className: "run-section", attrs: { open: "" } }, [
      el("summary", { text: "路由与决策" }),
      el("div", { className: "run-overview compact" }, [
        makeKv("active_agent", trace.active_agent || task.owner || "unknown"),
        makeKv("delegated_to", trace.delegated_to || task.owner || "unknown"),
      ]),
      el("pre", { className: "run-pre", text: trace.reasoning || "没有路由说明。" }),
    ]);
    taskContent.appendChild(route);
  }

  if (memorySummary) {
    taskContent.appendChild(
      el("details", { className: "run-section" }, [
        el("summary", { text: "记忆摘要" }),
        el("pre", { className: "run-pre", text: memorySummary }),
      ])
    );
  }

  const steps = el("div", { className: "task-steps" });
  (task.steps || []).forEach((step, index) => {
    const stepStatus = inferDisplayStatus(step);
    const args = step.args || trace.tool_calls?.[index]?.args || {};
    const head = el("div", { className: "step-head" }, [
      el("strong", { text: `${index + 1}. ${step.title || ""}` }),
      el("span", {
        className: `step-status ${stepStatus}`,
        text: statusLabel(stepStatus),
      }),
    ]);
    steps.appendChild(
      el("article", { className: "step" }, [
        head,
        el("p", { className: "step-args", text: argsPreview(args) }),
        el("p", { className: "step-detail", text: compactText(step.detail || "", 220) }),
      ])
    );
  });
  taskContent.appendChild(steps);
}

function renderTimeline(toolCalls) {
  clearChildren(timelineEl);
  if (!toolCalls.length) {
    timelineEl.appendChild(el("p", { className: "empty", text: "工具调用会显示在这里。" }));
    return;
  }
  toolCalls.forEach((item, index) => {
    const status = inferDisplayStatus(item);
    const detail = el("details", { className: `timeline-item tool-${status}` });
    if (index === 0) detail.open = true;
    const summary = el("summary", { className: "timeline-head" }, [
      el("strong", { text: `${index + 1}. ${item.name}` }),
      el("span", {
        className: `chip ${status}`,
        text: statusLabel(status),
      }),
    ]);
    detail.appendChild(summary);
    detail.appendChild(el("p", { className: "tool-preview", text: argsPreview(item.args || {}) }));
    detail.appendChild(el("div", { className: "tool-block-title", text: "调用参数" }));
    detail.appendChild(el("pre", { className: "tool-pre", text: formatJson(item.args || {}) }));
    detail.appendChild(el("div", { className: "tool-block-title", text: "返回结果" }));
    detail.appendChild(el("pre", { className: "tool-pre", text: item.result || "(empty)" }));
    timelineEl.appendChild(detail);
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
  setTaskStatus("running");
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
    setTaskStatus("failed");
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

  renderTask(response.task || {}, response.trace || {}, response.memory_summary || "");
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
        renderTask(data?.task || {}, data?.trace || {}, data?.memory_summary || "");
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
window.bishoujo.onPanelHistoryEvent?.((entry) => importPanelHistoryEntry(entry));
syncPanelHistoryEvents().then(() => {
  history = loadHistory(activeSessionId);
  reloadHistoryView();
});
pruneRollingHistories();
setInterval(pruneRollingHistories, 30_000);
loadCapabilities().catch((error) => {
  appendMessage("assistant", `能力加载失败：${error.message}`);
});
refreshReady();
setInterval(refreshReady, 30_000);

// ===========================================================================
// Settings modal
// ===========================================================================

let settingsState = null;        // last-loaded backend snapshot
let desktopPrefsState = null;    // last-loaded Electron desktop preferences
let providerListing = null;      // /api/settings/providers result
let pendingPatch = {};           // unsaved field deltas
let pendingDesktopPatch = {};    // unsaved desktop preference deltas
let activeTab = "general";

const DESKTOP_PREF_KEYS = new Set(["avatar", "petVoice", "voiceEnabled", "observeSpeechEnabled"]);
const PET_AVATAR_OPTIONS = [
  { value: "streamer", label: "虚拟主播" },
  { value: "swordswoman", label: "见习剑士" },
  { value: "cyber", label: "电子搭档" },
];
const PET_VOICE_OPTIONS = [
  { value: "warm-girl", label: "清亮女声" },
  { value: "sweet-lady", label: "甜美女声" },
  { value: "gentleman", label: "青年男声" },
  { value: "storyteller", label: "旁白男声" },
];

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
  pendingDesktopPatch = {};
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
window.bishoujo.onDesktopPrefsChanged?.((prefs) => {
  desktopPrefsState = { ...(desktopPrefsState || {}), ...prefs };
  if (!settingsBackdrop?.hidden && activeTab === "pet") {
    renderSettingsBody();
  }
});

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
    const [agent, providers, desktopPrefs] = await Promise.all([
      window.bishoujo.agentSettings(),
      window.bishoujo.listProviders(),
      window.bishoujo.desktopPrefs(),
    ]);
    settingsState = { ...agent };
    desktopPrefsState = { ...desktopPrefs };
    providerListing = providers;
    pendingPatch = {};
    pendingDesktopPatch = {};
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
  const agentPatch = { ...pendingPatch };
  const desktopPatch = { ...pendingDesktopPatch };
  if (!Object.keys(agentPatch).length && !Object.keys(desktopPatch).length) {
    setSettingsStatus("没有要保存的改动");
    return;
  }
  setSettingsStatus("正在保存...");
  try {
    const [updatedAgent, updatedDesktop] = await Promise.all([
      Object.keys(agentPatch).length
        ? window.bishoujo.updateAgentSettings(agentPatch)
        : Promise.resolve(settingsState),
      Object.keys(desktopPatch).length
        ? window.bishoujo.updateDesktopPrefs(desktopPatch)
        : Promise.resolve(desktopPrefsState),
    ]);
    settingsState = { ...updatedAgent };
    desktopPrefsState = { ...updatedDesktop };
    pendingPatch = {};
    pendingDesktopPatch = {};
    renderSettingsBody();
    refreshReady();
    setSettingsStatus("已保存", "ok");
    setTimeout(() => setSettingsStatus(""), 1500);
  } catch (error) {
    setSettingsStatus(describeError(error, "保存"), "error");
  }
}

function effectiveValue(key) {
  if (DESKTOP_PREF_KEYS.has(key)) {
    return key in pendingDesktopPatch ? pendingDesktopPatch[key] : desktopPrefsState?.[key];
  }
  return key in pendingPatch ? pendingPatch[key] : settingsState?.[key];
}

function setPending(key, value) {
  const isDesktopPref = DESKTOP_PREF_KEYS.has(key);
  const base = isDesktopPref ? desktopPrefsState : settingsState;
  const patch = isDesktopPref ? pendingDesktopPatch : pendingPatch;
  if (base && base[key] === value) {
    delete patch[key];
  } else {
    patch[key] = value;
  }
  // Update the dirty indicator without re-rendering the whole tab —
  // re-rendering would steal focus from the input the user is editing.
  const dirtyCount = Object.keys(pendingPatch).length + Object.keys(pendingDesktopPatch).length;
  if (dirtyCount) {
    setSettingsStatus(`有 ${dirtyCount} 项未保存`, "dirty");
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
  else if (activeTab === "pet") renderPetTab();
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

function renderPetTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "桌宠外观" }),
    selectField("形象", "也可以在桌宠右键菜单里快速切换。", "avatar", PET_AVATAR_OPTIONS),
    selectField("桌宠音色", "控制桌宠气泡回复使用的 TTS 音色。", "petVoice", PET_VOICE_OPTIONS),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "桌面说话行为" }),
    toggleField("允许桌宠语音回复", "关闭后桌宠仍会显示气泡和记录对话，但不播放语音。", "voiceEnabled"),
    toggleField("观察屏幕时朗读提醒", "关闭后持续陪伴只记录和显示重要观察，不主动读出来。", "observeSpeechEnabled"),
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
    `Pet:      ${desktopPrefsState?.avatar || "streamer"} · ${desktopPrefsState?.petVoice || "warm-girl"} · voice ${desktopPrefsState?.voiceEnabled ? "on" : "off"}`,
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
