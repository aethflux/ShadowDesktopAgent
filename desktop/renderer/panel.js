/**
 * panel.js — Shadow Agent Console
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
const SESSIONS_INDEX_KEY = "shadow.panel.sessions.v2";
const HISTORY_KEY_PREFIX = "shadow.panel.history.";
const HISTORY_LIMIT = 80;

const chatLog = document.getElementById("chatLog");
const chatTitle = document.getElementById("chatTitle");
const capabilitiesEl = document.getElementById("capabilities");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message");
const imageInput = document.getElementById("imageInput");
const attachBtn = document.getElementById("attachBtn");
const attachPreview = document.getElementById("attachPreview");
const taskContent = document.getElementById("taskContent");
const taskStatus = document.getElementById("taskStatus");
const timelineEl = document.getElementById("timeline");
const artifactsEl = document.getElementById("artifacts");
const sessionListEl = document.getElementById("sessionList");
const newSessionBtn = document.getElementById("newSessionBtn");
const streamingToggle = document.getElementById("streamingToggle");
const readyDot = document.getElementById("readyDot");
const workspaceEl = document.getElementById("workspace");
const traceToggle = document.getElementById("traceToggle");
const timelineSection = document.getElementById("timelineSection");
const artifactsSection = document.getElementById("artifactsSection");
const timelineCount = document.getElementById("timelineCount");
const artifactsCount = document.getElementById("artifactsCount");
const settingsBtn = document.getElementById("settingsBtn");
const settingsBackdrop = document.getElementById("settingsBackdrop");
const settingsClose = document.getElementById("settingsClose");
const settingsTabs = document.getElementById("settingsTabs");
const settingsBody = document.getElementById("settingsBody");
const settingsRefresh = document.getElementById("settingsRefresh");
const settingsSave = document.getElementById("settingsSave");
const settingsStatus = document.getElementById("settingsStatus");
const permissionBackdrop = document.getElementById("permissionBackdrop");
const permissionReason = document.getElementById("permissionReason");
const permissionPath = document.getElementById("permissionPath");
const permissionTip = document.getElementById("permissionTip");
const permissionCountdown = document.getElementById("permissionCountdown");
const permissionDeny = document.getElementById("permissionDeny");
const permissionOnce = document.getElementById("permissionOnce");
const permissionSession = document.getElementById("permissionSession");
const permissionAlways = document.getElementById("permissionAlways");

// Module-scope state for the active permission request: which request_id is
// awaiting decision and the countdown interval handle. Only one can be open
// at a time; subsequent requests are queued (we just overwrite the dialog
// since the broker won't send a new request_id until the previous one resolved).
let activePermissionRequestId = null;
let permissionCountdownTimer = null;

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
  sessionHistory.push({
    id, role, text, meta,
    ts: options.ts || Date.now(),
    source: options.source || "",
    // Small downscaled thumbnails (data URLs) so sent images survive a reload
    // without bloating localStorage with full-resolution payloads.
    attachments: Array.isArray(options.attachments) ? options.attachments : [],
  });
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

function recordHistory(role, text, meta = "", attachments = []) {
  recordHistoryForSession(activeSessionId, role, text, meta, { attachments });
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
    const events = await window.shadow.listPanelHistoryEvents?.();
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

function appendMessage(role, text, meta = "", { streaming = false, attachments = [] } = {}) {
  const msg = el("div", { className: `msg ${role}${streaming ? " streaming" : ""}` });
  if (Array.isArray(attachments) && attachments.length) {
    const wrap = el("div", { className: "msg-attachments" });
    for (const url of attachments) {
      if (url) wrap.appendChild(el("img", { className: "msg-thumb", attrs: { src: url, alt: "图片附件" } }));
    }
    if (wrap.children.length) msg.appendChild(wrap);
  }
  if (role === "assistant") {
    const body = el("div", { className: "msg-body" });
    body.innerHTML = renderMarkdown(text); // Safe: escape + whitelisted tags.
    msg.appendChild(body);
  } else if (text) {
    msg.appendChild(el("div", { className: "msg-text", text }));
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
    appendMessage(item.role, item.text, item.meta || "", { attachments: item.attachments || [] });
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
    const route = el("details", { className: "run-section" }, [
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
  if (timelineSection) timelineSection.open = toolCalls.length > 0;
  if (timelineCount) timelineCount.textContent = toolCalls.length ? String(toolCalls.length) : "";
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
  if (artifactsSection) artifactsSection.open = artifacts.length > 0;
  if (artifactsCount) artifactsCount.textContent = artifacts.length ? String(artifacts.length) : "";
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
  const features = capabilities.features || {};

  const group = (label, chips) =>
    el("div", { className: "cap-group" }, [
      el("span", { className: "cap-label", text: label }),
      el(
        "div",
        { className: "cap-chips" },
        chips.filter(Boolean).map((value) => el("span", { className: "chip", text: String(value) })),
      ),
    ]);

  const tools = (capabilities.tools || []).map(String);
  capabilitiesEl.appendChild(group("工具", tools.length ? tools : ["（暂无）"]));
  capabilitiesEl.appendChild(el("div", { className: "cap-divider" }));
  capabilitiesEl.appendChild(group("模型", [capabilities.provider, capabilities.model]));
  capabilitiesEl.appendChild(
    group("视觉 / 记忆", [
      `vision:${capabilities.vision_provider || "?"}`,
      `embed:${capabilities.embedding_provider || "?"}`,
    ]),
  );
  capabilitiesEl.appendChild(
    group("能力", [
      `vision:${features.vision ? "on" : "off"}`,
      `tts:${features.tts_engine || "browser-speech"}`,
      `memory:${features.semantic_memory ? "on" : "off"}`,
    ]),
  );
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
  const capabilities = await window.shadow.capabilities();
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
// Permission request dialog
// ---------------------------------------------------------------------------

// Show the dialog, prefill it with the broker's request, and start a visible
// countdown so the user knows how long they have. Returns immediately — the
// decision happens via button clicks which POST to /api/permissions/decide.
function openPermissionDialog(data) {
  if (!permissionBackdrop || !data?.request_id) return;
  activePermissionRequestId = data.request_id;
  permissionReason.textContent = data.reason || "Shadow 想访问一个目录";
  permissionPath.textContent = data.path || "(unknown path)";
  permissionTip.textContent = data.tool_name
    ? `工具：${data.tool_name}。如果这是你信任的目录，可以选择允许；否则建议拒绝。`
    : "如果这是你信任的目录，可以选择允许；否则建议拒绝。";

  const totalSeconds = Math.max(5, Number(data.timeout_seconds) || 60);
  let remaining = totalSeconds;
  const tick = () => {
    if (remaining <= 0) {
      permissionCountdown.textContent = "已超时，将自动按拒绝处理。";
      stopPermissionCountdown();
      // Don't auto-close — let the server's deny take effect; but reset state
      // so a new request can replace this one.
      return;
    }
    permissionCountdown.textContent = `${remaining} 秒后超时（按拒绝处理）`;
    remaining -= 1;
  };
  stopPermissionCountdown();
  tick();
  permissionCountdownTimer = setInterval(tick, 1000);

  permissionBackdrop.hidden = false;
}

function stopPermissionCountdown() {
  if (permissionCountdownTimer != null) {
    clearInterval(permissionCountdownTimer);
    permissionCountdownTimer = null;
  }
}

function closePermissionDialog() {
  if (!permissionBackdrop) return;
  stopPermissionCountdown();
  permissionBackdrop.hidden = true;
  activePermissionRequestId = null;
  permissionCountdown.textContent = "";
}

async function postPermissionDecision(decision) {
  const requestId = activePermissionRequestId;
  if (!requestId) return;
  // Optimistically close the dialog so the user sees an immediate response
  // even on a slow network. The backend resolves the future and the agent
  // proceeds — or in the deny case, surfaces a tool-error string.
  closePermissionDialog();
  try {
    await fetch(`${BACKEND_URL}/api/permissions/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, decision }),
    });
  } catch (err) {
    // The broker will time out and deny on its own; surface a hint so the
    // user knows their click might not have registered.
    console.warn("permission decide failed:", err);
  }
}

if (permissionDeny) permissionDeny.addEventListener("click", () => postPermissionDecision("deny"));
if (permissionOnce) permissionOnce.addEventListener("click", () => postPermissionDecision("allow_once"));
if (permissionSession) permissionSession.addEventListener("click", () => postPermissionDecision("allow_session"));
if (permissionAlways) permissionAlways.addEventListener("click", () => postPermissionDecision("allow_always"));

// ---------------------------------------------------------------------------
// Composer submit — streaming or one-shot depending on toggle
// ---------------------------------------------------------------------------

// ---- Composer attachments (image pick / paste / drop) ------------------- //
//
// One unified input: typed text, attached images, and voice all funnel through
// the same composer. Images can arrive via the 📎 picker, clipboard paste, or
// drag-drop; each shows as a removable preview chip before sending and inline
// in the user's bubble afterwards.
let pendingAttachments = []; // [{ kind, mime_type, data_url, thumb_url }]

// Downscale a data URL to a small thumbnail — used for the in-bubble image and
// for history persistence (full-res images would exhaust localStorage fast).
function downscaleImage(dataUrl, maxDim = 240) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
      const w = Math.max(1, Math.round(img.width * scale));
      const h = Math.max(1, Math.round(img.height * scale));
      try {
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL("image/jpeg", 0.82));
      } catch {
        resolve(dataUrl); // fall back to the original if canvas export fails
      }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

function renderAttachPreview() {
  clearChildren(attachPreview);
  if (!pendingAttachments.length) {
    attachPreview.hidden = true;
    return;
  }
  attachPreview.hidden = false;
  pendingAttachments.forEach((att, index) => {
    const chip = el("div", { className: "attach-chip" }, [
      el("img", { attrs: { src: att.thumb_url, alt: "待发送图片" } }),
    ]);
    const remove = el("button", {
      className: "attach-remove",
      attrs: { type: "button", title: "移除", "aria-label": "移除图片" },
      text: "✕",
    });
    remove.addEventListener("click", () => {
      pendingAttachments.splice(index, 1);
      renderAttachPreview();
    });
    chip.appendChild(remove);
    attachPreview.appendChild(chip);
  });
}

function clearAttachments() {
  pendingAttachments = [];
  renderAttachPreview();
}

async function addImageFiles(files) {
  const list = Array.from(files || []).filter((f) => f && f.type.startsWith("image/"));
  for (const file of list) {
    if (pendingAttachments.length >= 6) break; // sane cap per turn
    const dataUrl = await fileToDataUrl(file);
    const thumb = await downscaleImage(dataUrl);
    pendingAttachments.push({ kind: "image", mime_type: file.type, data_url: dataUrl, thumb_url: thumb });
  }
  renderAttachPreview();
}

attachBtn?.addEventListener("click", () => imageInput.click());
imageInput.addEventListener("change", () => {
  addImageFiles(imageInput.files);
  imageInput.value = ""; // let the same file be picked again later
});

// Paste an image directly into the message box.
messageInput.addEventListener("paste", (event) => {
  const items = event.clipboardData?.items;
  if (!items) return;
  const files = [];
  for (const item of items) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  if (files.length) {
    event.preventDefault();
    addImageFiles(files);
  }
});

// Drag-and-drop an image onto the composer.
composer.addEventListener("dragover", (event) => {
  if (event.dataTransfer?.types?.includes("Files")) {
    event.preventDefault();
    composer.classList.add("drag-over");
  }
});
composer.addEventListener("dragleave", (event) => {
  if (event.target === composer) composer.classList.remove("drag-over");
});
composer.addEventListener("drop", (event) => {
  composer.classList.remove("drag-over");
  const files = event.dataTransfer?.files;
  if (files && files.length) {
    event.preventDefault();
    addImageFiles(files);
  }
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  // Allow an image-only turn, but require at least text or an attachment.
  if (!text && !pendingAttachments.length) return;

  const sent = pendingAttachments.slice();
  const thumbs = sent.map((a) => a.thumb_url);

  appendMessage("user", text, "", { attachments: thumbs });
  recordHistory("user", text, "", thumbs);
  clearAttachments();
  setTaskStatus("running");
  composer.querySelector("button[type='submit']").disabled = true;

  const payload = {
    message: text,
    session_id: activeSessionId,
    attachments: sent.map((a) => ({ kind: "image", mime_type: a.mime_type, data_url: a.data_url })),
  };

  try {
    if (streamingToggle.checked) {
      await runStreaming(payload);
    } else {
      await runOneShot(payload);
    }
    messageInput.value = "";
  } catch (error) {
    setTaskStatus("failed");
    const reason = error && error.message ? error.message : String(error);
    appendMessage("assistant", `请求失败：${reason}`);
    recordHistory("assistant", `请求失败：${reason}`);
  } finally {
    composer.querySelector("button[type='submit']").disabled = false;
    taskStatus.classList.remove("streaming");
  }
});

async function runOneShot(payload) {
  const thinking = appendThinkingIndicator();
  let response;
  try {
    response = await window.shadow.chat(payload);
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
  // ``liveSteps`` is keyed by ``step_id`` so out-of-order tool_end events still
  // patch the right card. We also keep insertion order for deterministic
  // rendering — Map iteration honours insertion order in modern JS engines.
  const liveSteps = new Map();
  // Plan from the agent's pre-execution declaration (one ``plan`` event per
  // turn, before any tool_start). Held outside the steps map so re-renders
  // keep showing it even after later tool events repaint the timeline.
  let livePlan = null;
  let activeAgent = "";

  const upsertStep = (data, status) => {
    const id = data?.step_id;
    if (!id) return;
    const previous = liveSteps.get(id) || {};
    liveSteps.set(id, {
      step_id: id,
      index: data.index ?? previous.index ?? liveSteps.size + 1,
      name: data.name ?? previous.name ?? "(unknown tool)",
      args: data.args ?? previous.args ?? {},
      result: data.result ?? previous.result ?? "",
      success: typeof data.success === "boolean" ? data.success : previous.success,
      duration_ms: data.duration_ms ?? previous.duration_ms,
      // Preserve any partial stdout we collected from tool_progress events so
      // a tool_end repaint doesn't blow away the live tail.
      progress_lines: previous.progress_lines || [],
      status,
    });
    renderLiveTimeline(liveSteps, livePlan);
  };

  // tool_progress carries one stdout line at a time. Attach it to the step
  // identified by step_id, falling back to "the most recent running step"
  // for tools (e.g. legacy mocks) that don't propagate the id.
  const appendProgressLine = (data) => {
    let target = null;
    if (data?.step_id && liveSteps.has(data.step_id)) {
      target = liveSteps.get(data.step_id);
    } else {
      const stepsArr = Array.from(liveSteps.values());
      for (let i = stepsArr.length - 1; i >= 0; i -= 1) {
        if (stepsArr[i].status === "running") { target = stepsArr[i]; break; }
      }
    }
    if (!target) return;
    const lines = target.progress_lines || [];
    lines.push(String(data?.text || ""));
    // Cap so a runaway loop can't grow forever in the renderer's memory.
    if (lines.length > 200) lines.splice(0, lines.length - 200);
    target.progress_lines = lines;
    renderLiveTimeline(liveSteps, livePlan);
  };

  await streamChat(payload, {
    onEvent: ({ event, data }) => {
      if (event === "intent") {
        activeAgent = data?.delegated_to || activeAgent;
      } else if (event === "plan") {
        // Pre-execution checklist — render before any tools start.
        livePlan = data || null;
        renderLiveTimeline(liveSteps, livePlan);
      } else if (event === "tool_start") {
        // Show the tool card immediately with a running indicator. ``result``
        // is intentionally empty until tool_end arrives.
        upsertStep(data, "running");
      } else if (event === "tool_progress") {
        appendProgressLine(data);
      } else if (event === "tool_end") {
        upsertStep(data, data?.success === false ? "failed" : "completed");
      } else if (event === "tool_call") {
        // Back-compat aggregated event. If the live pipeline has already
        // recorded this step (by id), don't double-render it.
        if (data?.step_id && liveSteps.has(data.step_id)) return;
        upsertStep(data, data?.success === false ? "failed" : "completed");
      } else if (event === "permission_request") {
        // Surface the broker's ask. The user's click POSTs to /api/permissions/decide
        // which unblocks the waiting tool — this onEvent handler doesn't need
        // to await anything; the SSE stream stays open in the background.
        openPermissionDialog(data || {});
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
        // Prefer the authoritative tool_calls from the trace (it carries
        // step_id / duration_ms), fall back to whatever liveSteps captured.
        const finalCalls = data?.trace?.tool_calls?.length
          ? data.trace.tool_calls
          : Array.from(liveSteps.values());
        renderTimeline(finalCalls);
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

// Render in-flight tool steps as they happen. Distinct from ``renderTimeline``
// (which is for the final, post-turn aggregation): this one patches step cards
// in place so a "running" step can flip to "completed"/"failed" without
// reconstructing the DOM tree from scratch.
//
// ``plan`` is optional. When the agent emits a ``plan`` event before tool
// execution, we render a checklist on top of the timeline; checked items are
// the ones we've already covered (matched roughly by step index).
function renderLiveTimeline(stepsMap, plan) {
  clearChildren(timelineEl);

  const hasLiveContent = (plan && Array.isArray(plan.plan) && plan.plan.length) || (stepsMap && stepsMap.size);
  if (timelineSection && hasLiveContent) timelineSection.open = true;
  if (timelineCount) timelineCount.textContent = stepsMap && stepsMap.size ? String(stepsMap.size) : "";

  if (plan && Array.isArray(plan.plan) && plan.plan.length) {
    const checklist = el("div", { className: "live-plan" });
    if (plan.summary) {
      checklist.appendChild(el("p", { className: "live-plan-summary", text: plan.summary }));
    }
    const completedCount = stepsMap
      ? Array.from(stepsMap.values()).filter((s) => s.status === "completed").length
      : 0;
    const list = el("ol", { className: "live-plan-list" });
    plan.plan.forEach((stepText, index) => {
      const isDone = index < completedCount;
      const item = el("li", { className: "live-plan-item" + (isDone ? " done" : "") }, [
        el("span", { className: "live-plan-mark", text: isDone ? "✓" : String(index + 1) }),
        el("span", { className: "live-plan-text", text: String(stepText) }),
      ]);
      list.appendChild(item);
    });
    checklist.appendChild(list);
    timelineEl.appendChild(checklist);
  }

  if (!stepsMap || !stepsMap.size) {
    if (!plan || !Array.isArray(plan.plan) || !plan.plan.length) {
      timelineEl.appendChild(el("p", { className: "empty", text: "工具调用会显示在这里。" }));
    }
    return;
  }
  let position = 0;
  for (const step of stepsMap.values()) {
    position += 1;
    const status = step.status || "running";
    const detail = el("details", { className: `timeline-item tool-${status}` });
    if (status === "running" || position === stepsMap.size) detail.open = true;
    const headChildren = [
      el("strong", { text: `${step.index || position}. ${step.name}` }),
      el("span", {
        className: `chip ${status}`,
        text: statusLabel(status),
      }),
    ];
    if (typeof step.duration_ms === "number" && status !== "running") {
      headChildren.push(
        el("span", { className: "step-duration", text: `${step.duration_ms} ms` }),
      );
    }
    const summary = el("summary", { className: "timeline-head" }, headChildren);
    detail.appendChild(summary);
    detail.appendChild(el("p", { className: "tool-preview", text: argsPreview(step.args || {}) }));
    detail.appendChild(el("div", { className: "tool-block-title", text: "调用参数" }));
    detail.appendChild(el("pre", { className: "tool-pre", text: formatJson(step.args || {}) }));

    // Live stdout tail — populated by tool_progress events. Show below the
    // args block (above the final result) so the user can watch a long
    // command without expanding extra panels.
    const progressLines = step.progress_lines || [];
    if (progressLines.length) {
      detail.appendChild(el("div", { className: "tool-block-title", text: "实时输出" }));
      detail.appendChild(
        el("pre", { className: "tool-pre tool-progress-tail", text: progressLines.join("\n") }),
      );
    }

    if (status === "running") {
      detail.appendChild(el("div", { className: "tool-running-hint" }, [
        el("span", { className: "running-spinner" }),
        el("span", { text: " 工具正在执行…" }),
      ]));
    } else {
      detail.appendChild(el("div", { className: "tool-block-title", text: "返回结果" }));
      detail.appendChild(el("pre", { className: "tool-pre", text: step.result || "(empty)" }));
    }
    timelineEl.appendChild(detail);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

renderSessions();
reloadHistoryView();
window.shadow.onPanelHistoryEvent?.((entry) => importPanelHistoryEntry(entry));
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

// Trace panel show/hide — lets the user reclaim width for the conversation.
// The choice is persisted so it survives reopening the console.
const TRACE_COLLAPSED_KEY = "shadow.tracePanelCollapsed";

function applyTracePref() {
  const collapsed = localStorage.getItem(TRACE_COLLAPSED_KEY) === "1";
  workspaceEl?.classList.toggle("trace-collapsed", collapsed);
  if (traceToggle) {
    traceToggle.textContent = collapsed ? "◨" : "◧";
    traceToggle.classList.toggle("active", !collapsed);
  }
}

traceToggle?.addEventListener("click", () => {
  const collapsed = !workspaceEl.classList.contains("trace-collapsed");
  localStorage.setItem(TRACE_COLLAPSED_KEY, collapsed ? "1" : "0");
  applyTracePref();
});

applyTracePref();

// ===========================================================================
// Settings modal
// ===========================================================================

let settingsState = null;        // last-loaded backend snapshot
let desktopPrefsState = null;    // last-loaded Electron desktop preferences
let providerListing = null;      // /api/settings/providers result
let personaPresets = null;       // /api/persona/presets result (cached)
let pendingPatch = {};           // unsaved field deltas
let pendingDesktopPatch = {};    // unsaved desktop preference deltas
let activeTab = "general";

const DESKTOP_PREF_KEYS = new Set(["avatar", "petVoice", "voiceEnabled", "observeSpeechEnabled"]);
const PET_AVATAR_OPTIONS = [
  { value: "streamer", label: "虚拟主播" },
  { value: "swordswoman", label: "见习剑士" },
  { value: "cyber", label: "电子搭档" },
  { value: "senpai", label: "学姐" },
];
// Persona archetype ↔ pet avatar sprite. The catalogue is curated so every
// persona has exactly one sprite and vice-versa, which lets us bind the two
// directions: picking a personality sets the on-screen form, and picking a
// form (桌宠 tab / right-click menu) applies the matching personality.
const ARCHETYPE_TO_AVATAR = {
  swordswoman_partner: "swordswoman",
  study_senpai: "senpai",
  cyber_ai: "cyber",
  genki_kouhai: "streamer",
};
const AVATAR_TO_ARCHETYPE = Object.fromEntries(
  Object.entries(ARCHETYPE_TO_AVATAR).map(([archetype, avatar]) => [avatar, archetype]),
);
function avatarSpriteUrl(avatarId) {
  return `./assets/avatars/shadow-${avatarId}.png`;
}
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
window.shadow.onOpenSettings?.((tab) => openSettings(tab || "general"));
window.shadow.onDesktopPrefsChanged?.((prefs) => {
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
      window.shadow.agentSettings(),
      window.shadow.listProviders(),
      window.shadow.desktopPrefs(),
    ]);
    settingsState = { ...agent };
    desktopPrefsState = { ...desktopPrefs };
    providerListing = providers;
    pendingPatch = {};
    pendingDesktopPatch = {};
    // Persona presets are cached after the first successful fetch — they
    // never change at runtime. A failure here is non-fatal: the tab falls
    // back to "no presets available" but the form fields still work.
    if (personaPresets == null) {
      try {
        const resp = await fetch(`${BACKEND_URL}/api/persona/presets`);
        if (resp.ok) personaPresets = await resp.json();
      } catch (err) {
        console.warn("persona presets fetch failed:", err);
      }
    }
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
        ? window.shadow.updateAgentSettings(agentPatch)
        : Promise.resolve(settingsState),
      Object.keys(desktopPatch).length
        ? window.shadow.updateDesktopPrefs(desktopPatch)
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

function toggleField(label, hint, key, { disabled = false, onChange = null } = {}) {
  const wrapper = el("div", { className: `field field-toggle${disabled ? " disabled" : ""}` });
  const labelEl = el("div", { className: "field-toggle-text" }, [
    el("span", { className: "field-label", text: label }),
    hint ? el("span", { className: "field-hint", text: hint }) : null,
  ]);
  const sw = el("label", { className: "switch" });
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!effectiveValue(key);
  input.disabled = disabled;
  input.addEventListener("change", () => {
    setPending(key, input.checked);
    if (onChange) onChange(input.checked);
  });
  const slider = el("span", { className: "slider" });
  sw.appendChild(input);
  sw.appendChild(slider);
  wrapper.appendChild(labelEl);
  wrapper.appendChild(sw);
  return wrapper;
}

function selectField(label, hint, key, options, { onChange = null } = {}) {
  const select = document.createElement("select");
  for (const opt of options) {
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    if (opt.disabled) o.disabled = true;
    select.appendChild(o);
  }
  select.value = String(effectiveValue(key) ?? "");
  select.addEventListener("change", () => {
    setPending(key, select.value);
    if (onChange) onChange(select.value);
  });
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
  else if (activeTab === "persona") renderPersonaTab();
  else if (activeTab === "voice") renderVoiceTab();
  else if (activeTab === "memory") renderMemoryTab();
  else if (activeTab === "permissions") renderPermissionsTab();
  else renderAboutTab();
}

function providerOptions(filter = () => true) {
  if (!providerListing) return [{ value: "", label: "(provider 列表加载中)" }];
  return providerListing.providers.filter(filter).map((p) => ({
    value: p.id,
    label: `${p.display_name} · ${p.configured ? "已配置" : "未配置 API key"}`,
    disabled: !p.configured,
  }));
}

function providerById(providerId) {
  return providerListing?.providers?.find((p) => p.id === providerId) || null;
}

function providerDisplay(providerId) {
  return providerById(providerId)?.display_name || providerId || "未选择";
}

function providerDefaultModel(providerId) {
  return providerById(providerId)?.default_model || "";
}

function modelFieldForProvider(providerId) {
  return {
    openai: "openai_model",
    anthropic: "anthropic_model",
    vllm: "vllm_model",
    minimax: "minimax_model",
    modelscope: "modelscope_model",
  }[providerId] || "model";
}

function settingsSummaryCard(rows) {
  return el("div", { className: "settings-summary-card" }, rows.map((row) =>
    el("div", { className: "settings-summary-row" }, [
      el("span", { text: row.label }),
      el("strong", { text: row.value || "未设置" }),
    ])
  ));
}

function renderGeneralTab() {
  const selectedProvider = effectiveValue("provider") || settingsState.provider || "minimax";
  const selectedVisionProvider = effectiveValue("vision_provider") || settingsState.vision_provider || "modelscope";
  const chatModelKey = modelFieldForProvider(selectedProvider);
  const chatDefaultModel = providerDefaultModel(selectedProvider);
  const isAnthropicProvider = selectedProvider === "anthropic";
  const sections = [
    el("h3", { text: "当前模型" }),
    settingsSummaryCard([
      {
        label: "对话与工具调用",
        value: `${providerDisplay(selectedProvider)} / ${effectiveValue(chatModelKey) || chatDefaultModel || settingsState.model}`,
      },
      {
        label: "屏幕观察与图片理解",
        value: `${providerDisplay(selectedVisionProvider)} / ${effectiveValue("vision_model") || providerDefaultModel(selectedVisionProvider)}`,
      },
    ]),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "对话与任务模型" }),
    selectField(
      "模型服务",
      "控制普通聊天、任务规划和工具调用时使用的主模型。",
      "provider",
      providerOptions(),
      { onChange: () => renderSettingsBody() },
    ),
    textField(
      "模型 ID",
      chatDefaultModel
        ? `留空则使用 ${providerDisplay(selectedProvider)} 的默认模型：${chatDefaultModel}`
        : "填写当前模型服务支持的模型 ID。",
      chatModelKey,
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "视觉模型" }),
    selectField(
      "视觉服务",
      "控制截图分析、屏幕观察和图片理解。",
      "vision_provider",
      providerOptions((p) => p.supports_vision),
      { onChange: () => renderSettingsBody() },
    ),
    textField(
      "视觉模型 ID",
      "建议使用支持图片输入的模型，例如 Qwen/Qwen3-VL-8B-Instruct。",
      "vision_model",
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "稳定性" }),
    textField("失败重试次数", "遇到 5xx、429 或网络错误时最多重试几次。", "model_max_retries", { type: "number" }),
    textField("重试间隔基准（秒）", "实际等待时间会按指数退避增长。", "model_retry_backoff_seconds", { type: "number" }),
  ];

  if (isAnthropicProvider) {
    sections.push(
      el("div", { className: "settings-divider" }),
      el("h3", { text: "Anthropic 专属" }),
      toggleField(
        "启用 Prompt 缓存",
        "只在 Anthropic provider 下生效，用于缓存稳定的系统提示词前缀。",
        "enable_prompt_cache",
      ),
      textField(
        "回复上限",
        "只影响 Anthropic 模型的 max_tokens；其它 Provider 不读取此项。",
        "anthropic_max_tokens",
        { type: "number" },
      ),
    );
  }

  const groups = el("div", { className: "settings-section" }, sections);
  settingsBody.appendChild(groups);
}

function renderPetTab() {
  const voiceEnabled = !!effectiveValue("voiceEnabled");
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "桌宠外观" }),
    selectField(
      "形象（与性格绑定）",
      "形象和性格是同一个身份：切换形象会自动套用对应人设，想细调性格请到“性格”页。也可在桌宠右键菜单快速切换。",
      "avatar",
      PET_AVATAR_OPTIONS,
      {
        onChange: (value) => {
          const archetype = AVATAR_TO_ARCHETYPE[value];
          const preset = personaPresets?.presets?.find((p) => p.id === archetype);
          if (preset) {
            commitPersonaConfig(() => ({ ...preset.config }));
            setSettingsStatus(`已切换形象并同步人设：${preset.label}（保存后生效）`, "dirty");
          }
        },
      },
    ),
    selectField("桌宠音色", "控制桌宠气泡回复使用的 TTS 音色。", "petVoice", PET_VOICE_OPTIONS),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "桌面说话行为" }),
    toggleField(
      "桌宠语音总开关",
      "关闭后桌宠仍会显示气泡和记录对话，但不会播放任何语音。",
      "voiceEnabled",
      { onChange: () => renderSettingsBody() },
    ),
    toggleField(
      "持续陪伴时朗读观察结果",
      voiceEnabled
        ? "只控制屏幕观察产生的提醒。关闭后，主动聊天仍可语音回复。"
        : "语音总开关关闭时，此项不会生效。",
      "observeSpeechEnabled",
      { disabled: !voiceEnabled },
    ),
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

// ---- Permissions tab ----------------------------------------------------
//
// The two lists are stored on the backend as JSON-encoded strings (see
// ``workspace_allowlist_json`` / ``workspace_denylist_json`` in Settings).
// We parse them on the way in, present them as removable chips, and re-
// serialise on every mutation so the change is detected by ``setPending``
// and persisted on Save.

function parsePathListField(name) {
  const raw = effectiveValue(name) ?? "[]";
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((p) => typeof p === "string") : [];
  } catch {
    return [];
  }
}

function setPathListField(name, list) {
  setPending(name, JSON.stringify(list));
  renderSettingsBody();  // re-render to reflect chip changes
}

function renderPathList(name, locked) {
  const wrapper = el("div", { className: "permissions-list" });
  const items = parsePathListField(name);
  if (!items.length) {
    wrapper.appendChild(
      el("p", { className: "empty", text: locked ? "（默认黑名单为空，已使用内置防护）" : "（暂无白名单条目）" })
    );
    return wrapper;
  }
  items.forEach((path, index) => {
    const row = el("div", { className: `permission-row${locked ? " locked" : ""}` }, [
      el("code", { text: path }),
      el("button", {
        className: "ghost compact",
        text: "移除",
        attrs: { type: "button" },
      }),
    ]);
    const removeBtn = row.querySelector("button");
    removeBtn.addEventListener("click", () => {
      const next = parsePathListField(name).filter((_, i) => i !== index);
      setPathListField(name, next);
    });
    wrapper.appendChild(row);
  });
  return wrapper;
}

function renderPathAddRow(name, placeholder) {
  const input = el("input", {
    attrs: { type: "text", placeholder, "aria-label": "新增路径" },
  });
  const button = el("button", {
    className: "ghost",
    text: "添加",
    attrs: { type: "button" },
  });
  button.addEventListener("click", () => {
    const value = input.value.trim();
    if (!value) return;
    const list = parsePathListField(name);
    if (list.includes(value)) return;
    list.push(value);
    setPathListField(name, list);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      button.click();
    }
  });
  return el("div", { className: "permission-add-row" }, [input, button]);
}

// ---- Persona tab --------------------------------------------------------
//
// PersonaConfig is stored as a single JSON blob in `persona_config_json`.
// The UI parses it into a working object, mutates fields in place, and
// re-serialises on every change so `setPending` records a single
// `persona_config_json` delta.

function personaConfigDefaults() {
  return {
    name: "Shadow",
    archetype: "swordswoman_partner",
    personality_traits: ["温柔", "坚定", "略带俏皮", "保护欲强"],
    speaking_style: "简洁有力，温暖有节制",
    address_user_as: "你",
    backstory: "",
    forbidden_topics: [],
    catchphrases: [],
    emoji_usage: "occasional",
    response_length: "balanced",
    custom_system_prompt: "",
  };
}

function effectivePersonaConfig() {
  const raw = effectiveValue("persona_config_json") ?? "";
  if (!raw || !String(raw).trim()) return personaConfigDefaults();
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return { ...personaConfigDefaults(), ...parsed };
    }
  } catch {
    // fall through
  }
  return personaConfigDefaults();
}

function commitPersonaConfig(mutator, { rerender = false } = {}) {
  const next = mutator(effectivePersonaConfig());
  setPending("persona_config_json", JSON.stringify(next));
  if (rerender) renderSettingsBody();
}

function personaTextInput(label, hint, configKey, { multiline = false } = {}) {
  const input = multiline ? document.createElement("textarea") : document.createElement("input");
  if (!multiline) input.type = "text";
  if (multiline) input.rows = 4;
  input.value = effectivePersonaConfig()[configKey] ?? "";
  // Don't re-render on input — that would steal focus from the textbox.
  input.addEventListener("input", () => {
    commitPersonaConfig((c) => ({ ...c, [configKey]: input.value }));
  });
  return fieldGroup(label, hint, input);
}

function personaChipList(label, hint, configKey, placeholder) {
  const wrapper = el("div", { className: "field" });
  wrapper.appendChild(el("span", { className: "field-label", text: label }));
  if (hint) wrapper.appendChild(el("p", { className: "field-hint", text: hint }));
  const items = effectivePersonaConfig()[configKey] || [];
  const chips = el("div", { className: "persona-chips" });
  if (!items.length) {
    chips.appendChild(el("span", { className: "empty-inline", text: "（暂无）" }));
  }
  items.forEach((item, index) => {
    const removeBtn = el("button", {
      className: "persona-chip-remove",
      text: "×",
      attrs: { type: "button", "aria-label": `移除 ${item}` },
    });
    removeBtn.addEventListener("click", () => {
      commitPersonaConfig(
        (c) => ({
          ...c,
          [configKey]: (c[configKey] || []).filter((_, i) => i !== index),
        }),
        { rerender: true },
      );
    });
    chips.appendChild(el("span", { className: "persona-chip" }, [
      el("span", { text: item }),
      removeBtn,
    ]));
  });
  wrapper.appendChild(chips);

  const addInput = document.createElement("input");
  addInput.type = "text";
  addInput.placeholder = placeholder;
  const addBtn = el("button", {
    className: "ghost",
    text: "添加",
    attrs: { type: "button" },
  });
  const commitAdd = () => {
    const value = addInput.value.trim();
    if (!value) return;
    commitPersonaConfig(
      (c) => {
        const list = c[configKey] || [];
        if (list.includes(value)) return c;
        return { ...c, [configKey]: [...list, value] };
      },
      { rerender: true },
    );
  };
  addBtn.addEventListener("click", commitAdd);
  addInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitAdd();
    }
  });
  wrapper.appendChild(el("div", { className: "persona-add-row" }, [addInput, addBtn]));
  return wrapper;
}

function personaRadio(label, hint, configKey, options) {
  const wrapper = el("div", { className: "field" });
  wrapper.appendChild(el("span", { className: "field-label", text: label }));
  if (hint) wrapper.appendChild(el("p", { className: "field-hint", text: hint }));
  const current = effectivePersonaConfig()[configKey];
  const group = el("div", { className: "persona-radio-group" });
  for (const opt of options) {
    const id = `persona-${configKey}-${opt.value}`;
    const labelEl = el("label", { className: "persona-radio" + (current === opt.value ? " selected" : "") });
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = `persona-${configKey}`;
    radio.value = opt.value;
    radio.id = id;
    radio.checked = current === opt.value;
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      commitPersonaConfig(
        (c) => ({ ...c, [configKey]: opt.value }),
        { rerender: true },
      );
    });
    labelEl.appendChild(radio);
    labelEl.appendChild(el("span", { text: opt.label }));
    group.appendChild(labelEl);
  }
  wrapper.appendChild(group);
  return wrapper;
}

function renderPersonaPresetRow() {
  const row = el("div", { className: "persona-presets" });
  if (!personaPresets || !Array.isArray(personaPresets.presets) || !personaPresets.presets.length) {
    row.appendChild(el("p", { className: "empty", text: "（预设加载中或后端未返回，可手动编辑下面的字段）" }));
    return row;
  }
  // Highlight the preset whose archetype matches the current config — coarse
  // but useful: lets the user see "I'm currently on the 学姐 preset" at a glance.
  const currentArchetype = effectivePersonaConfig().archetype;
  for (const preset of personaPresets.presets) {
    const isActive = preset.id === currentArchetype;
    // Each preset carries its bound avatar so the card shows the actual face,
    // making the persona↔form link visible at a glance. Drop the thumbnail if
    // the sprite ever fails to load rather than showing a broken image.
    const avatarId = ARCHETYPE_TO_AVATAR[preset.id];
    let avatarImg = null;
    if (avatarId) {
      avatarImg = el("img", {
        className: "persona-preset-avatar",
        attrs: { src: avatarSpriteUrl(avatarId), alt: preset.label, loading: "lazy" },
      });
      avatarImg.addEventListener("error", () => avatarImg.remove());
    }
    const card = el(
      "button",
      {
        className: "persona-preset" + (isActive ? " active" : ""),
        attrs: { type: "button", title: preset.description || preset.label },
      },
      [
        avatarImg,
        el("div", { className: "persona-preset-text" }, [
          el("strong", { text: preset.label }),
          el("span", { className: "persona-preset-desc", text: preset.description || "" }),
        ]),
      ],
    );
    card.addEventListener("click", () => {
      commitPersonaConfig(() => ({ ...preset.config }), { rerender: true });
      // Keep the on-screen sprite in sync with the chosen personality. Only
      // for archetypes that have a matching sprite; others keep the current
      // avatar so we never show a mismatched image. ``setPending`` routes the
      // "avatar" key into the desktop-prefs patch (it's in DESKTOP_PREF_KEYS),
      // so it ships together with the persona on save.
      const mappedAvatar = ARCHETYPE_TO_AVATAR[preset.id];
      const syncedAvatar = mappedAvatar && effectiveValue("avatar") !== mappedAvatar;
      if (syncedAvatar) setPending("avatar", mappedAvatar);
      setSettingsStatus(
        `已套用预设：${preset.label}${syncedAvatar ? "，已同步桌宠形象" : ""}（保存后生效）`,
        "dirty",
      );
    });
    row.appendChild(card);
  }
  return row;
}

function renderPersonaTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "预设" }),
    el("p", {
      className: "field-hint",
      text: "点击一个预设即可一键填充下面的字段。你也可以基于预设再修改任意字段。修改后点保存生效。",
    }),
    renderPersonaPresetRow(),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "基本身份" }),
    personaTextInput("名字", "桌宠对自己的称呼。", "name"),
    personaTextInput("如何称呼用户", "例如 你 / 主人 / 同学 / 大人。", "address_user_as"),
    personaTextInput(
      "背景故事",
      "一段自由文本，不要冒充受版权保护的角色。可以留空。",
      "backstory",
      { multiline: true },
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "性格与说话风格" }),
    personaChipList(
      "性格特质",
      "用短词描述，例如 温柔 / 严谨 / 元气。回车快速添加。",
      "personality_traits",
      "添加一个性格特质",
    ),
    personaTextInput("说话风格", "例如：简洁有力 / 知识密度高 / 短句感叹号多。", "speaking_style"),
    personaChipList(
      "口头禅",
      "桌宠常说的短语，会自然出现在回复中。",
      "catchphrases",
      "添加一个口头禅",
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "回复偏好" }),
    personaRadio("Emoji 使用", "", "emoji_usage", [
      { value: "none", label: "不使用" },
      { value: "occasional", label: "偶尔使用" },
      { value: "frequent", label: "频繁使用" },
    ]),
    personaRadio("回复长度", "", "response_length", [
      { value: "concise", label: "简洁" },
      { value: "balanced", label: "平衡" },
      { value: "detailed", label: "详细" },
    ]),
    personaChipList(
      "禁忌话题",
      "桌宠会拒绝主动谈论的话题。回车快速添加。",
      "forbidden_topics",
      "添加一个禁忌话题",
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "高级（可选）" }),
    personaTextInput(
      "自定义补充指令",
      "追加在系统 prompt 末尾，覆盖不了角色职责，但可以加任意额外要求。留空即可。",
      "custom_system_prompt",
      { multiline: true },
    ),
  ]);
  settingsBody.appendChild(groups);
}

function renderPermissionsTab() {
  const groups = el("div", { className: "settings-section" }, [
    el("h3", { text: "白名单（自动放行）" }),
    el("p", {
      className: "field-hint",
      text: "桌宠在这些目录及其子目录里的文件操作不会触发询问。可以手填路径，也可以在弹窗里点“永久允许”自动加入。",
    }),
    renderPathList("workspace_allowlist_json", false),
    renderPathAddRow(
      "workspace_allowlist_json",
      "例如 E:\\projects 或 ~/Documents/sandbox",
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "黑名单（永远拒绝）" }),
    el("p", {
      className: "field-hint",
      text: "在这里的目录就算手动加进白名单也会被覆盖。系统目录、密钥目录建议保留默认。",
    }),
    renderPathList("workspace_denylist_json", true),
    renderPathAddRow(
      "workspace_denylist_json",
      "例如 C:\\Users\\Public 或 ~/.config",
    ),
    el("div", { className: "settings-divider" }),
    el("h3", { text: "询问行为" }),
    toggleField(
      "需要确认时弹窗询问",
      "关闭后，白名单外的访问会直接被拒绝（不弹窗）。建议保持开启。",
      "require_path_confirmation",
    ),
    textField(
      "等待用户决定的超时秒数",
      "用户没有在此时间内点选时，自动按拒绝处理。",
      "permission_request_timeout_seconds",
      { type: "number" },
    ),
  ]);
  settingsBody.appendChild(groups);
}
