import { app, BrowserWindow, ipcMain, Menu, screen, shell, MenuItemConstructorOptions } from "electron";
import path from "node:path";
import fs from "node:fs";

const BACKEND_URL = process.env.BISHOUJO_AGENT_API ?? "http://127.0.0.1:8787";

type Point = { x: number; y: number };
type DragState = { startMouse: Point; startWindow: Point };

/** Window-local UX preferences. Persisted to ``userData/desktop-prefs.json``. */
type DesktopPrefs = {
  avatar: string;
  petVoice: string;
  voiceEnabled: boolean;
  observeSpeechEnabled: boolean;
};

type PanelHistoryEntry = {
  id?: string;
  sessionId: string;
  title?: string;
  fixed?: boolean;
  role: "user" | "assistant";
  text: string;
  meta?: string;
  ts?: number;
  retentionMs?: number;
};

type AgentSettingsView = Record<string, unknown>;
type ProviderListing = {
  current: string;
  current_vision: string;
  providers: Array<{
    id: string;
    display_name: string;
    configured: boolean;
    default_model: string;
    supports_vision: boolean;
  }>;
};

const DEFAULT_PREFS: DesktopPrefs = {
  avatar: "streamer",
  petVoice: "warm-girl",
  voiceEnabled: true,
  observeSpeechEnabled: true
};

const AVATAR_OPTIONS = [
  { id: "streamer", label: "虚拟主播" },
  { id: "swordswoman", label: "见习剑士" },
  { id: "cyber", label: "电子搭档" }
];

const PET_VOICE_OPTIONS = [
  { id: "warm-girl", label: "清亮女声" },
  { id: "sweet-lady", label: "甜美女声" },
  { id: "gentleman", label: "青年男声" },
  { id: "storyteller", label: "旁白男声" }
];

const CHAT_WINDOW_WIDTH = 300;
const CHAT_WINDOW_HEIGHT = 62;
const WINDOW_MARGIN = 24;

let petWindow: BrowserWindow | null = null;
let chatWindow: BrowserWindow | null = null;
let panelWindow: BrowserWindow | null = null;
let petDragState: DragState | null = null;
let companionWatching = false;
let desktopPrefs: DesktopPrefs = { ...DEFAULT_PREFS };

app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
}

// ---------------------------------------------------------------------------
// Desktop preferences persistence
// ---------------------------------------------------------------------------

function prefsPath(): string {
  return path.join(app.getPath("userData"), "desktop-prefs.json");
}

function panelHistoryPath(): string {
  return path.join(app.getPath("userData"), "panel-history-events.json");
}

function loadPrefs(): DesktopPrefs {
  try {
    const raw = fs.readFileSync(prefsPath(), "utf-8");
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function savePrefs(prefs: DesktopPrefs): void {
  try {
    fs.mkdirSync(path.dirname(prefsPath()), { recursive: true });
    fs.writeFileSync(prefsPath(), JSON.stringify(prefs, null, 2), "utf-8");
  } catch (error) {
    console.warn("Could not save desktop prefs:", error);
  }
}

function applyPrefsPatch(patch: Partial<DesktopPrefs>): DesktopPrefs {
  desktopPrefs = { ...desktopPrefs, ...patch };
  savePrefs(desktopPrefs);
  sendToWindow(petWindow, "app:prefs-changed", desktopPrefs);
  sendToWindow(panelWindow, "app:prefs-changed", desktopPrefs);
  return desktopPrefs;
}

function loadPanelHistoryEvents(): PanelHistoryEntry[] {
  try {
    const raw = fs.readFileSync(panelHistoryPath(), "utf-8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const now = Date.now();
    const retained = parsed.filter((item) => {
      const retention = Number(item.retentionMs || 0);
      return !retention || now - Number(item.ts || 0) <= retention;
    });
    if (retained.length !== parsed.length) {
      savePanelHistoryEvents(retained);
    }
    return retained;
  } catch {
    return [];
  }
}

function savePanelHistoryEvents(events: PanelHistoryEntry[]): void {
  try {
    fs.mkdirSync(path.dirname(panelHistoryPath()), { recursive: true });
    fs.writeFileSync(panelHistoryPath(), JSON.stringify(events.slice(-500), null, 2), "utf-8");
  } catch (error) {
    console.warn("Could not save panel history events:", error);
  }
}

function normalizePanelHistoryEntry(entry: PanelHistoryEntry): PanelHistoryEntry {
  const ts = Number(entry.ts || Date.now());
  return {
    id: entry.id || `${entry.sessionId}-${ts}-${Math.random().toString(36).slice(2, 8)}`,
    sessionId: entry.sessionId,
    title: entry.title,
    fixed: !!entry.fixed,
    role: entry.role === "user" ? "user" : "assistant",
    text: String(entry.text || ""),
    meta: entry.meta ? String(entry.meta) : "",
    ts,
    retentionMs: entry.retentionMs ? Number(entry.retentionMs) : undefined,
  };
}

function recordPanelHistoryEvent(entry: PanelHistoryEntry): PanelHistoryEntry {
  const normalized = normalizePanelHistoryEntry(entry);
  const now = Date.now();
  const events = loadPanelHistoryEvents()
    .filter((item) => {
      const retention = Number(item.retentionMs || 0);
      return !retention || now - Number(item.ts || 0) <= retention;
    })
    .filter((item) => item.id !== normalized.id);
  events.push(normalized);
  savePanelHistoryEvents(events);
  sendToWindow(panelWindow, "app:panel-history-event", normalized);
  return normalized;
}

// ---------------------------------------------------------------------------
// Window construction
// ---------------------------------------------------------------------------

function attachWindowDiagnostics(name: string, win: BrowserWindow): void {
  win.webContents.on("did-finish-load", () => {
    console.log(`[${name}] did-finish-load`);
  });
  win.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    console.log(`[${name}][console:${level}] ${message} (${sourceId}:${line})`);
  });
  win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    console.error(
      `[${name}] did-fail-load code=${errorCode} description=${errorDescription} url=${validatedURL} mainFrame=${isMainFrame}`
    );
  });
  win.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[${name}] render-process-gone ${JSON.stringify(details)}`);
  });
  win.webContents.on("preload-error", (_event, file, error) => {
    console.error(`[${name}] preload-error path=${file} error=${error}`);
  });
}

function createPetWindow(): BrowserWindow {
  const display = screen.getPrimaryDisplay().workAreaSize;
  const width = 240;
  const height = 350;
  const win = new BrowserWindow({
    width,
    height,
    x: display.width - width - 24,
    y: display.height - height - 36,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js")
    }
  });

  win.loadFile(path.join(__dirname, "../renderer/pet.html"));
  attachWindowDiagnostics("pet", win);
  win.on("closed", () => {
    if (petWindow === win) {
      petWindow = null;
    }
  });
  return win;
}

function clampToRange(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function chatHomePosition(): Point {
  const petBounds = isUsableWindow(petWindow) ? petWindow.getBounds() : null;
  const display = petBounds
    ? screen.getDisplayMatching(petBounds).workArea
    : screen.getPrimaryDisplay().workArea;
  const preferredX = petBounds
    ? petBounds.x + Math.round((petBounds.width - CHAT_WINDOW_WIDTH) / 2)
    : display.x + display.width - CHAT_WINDOW_WIDTH - WINDOW_MARGIN;
  const preferredY = petBounds
    ? petBounds.y + petBounds.height + 8
    : display.y + display.height - CHAT_WINDOW_HEIGHT - WINDOW_MARGIN;
  return {
    x: clampToRange(
      preferredX,
      display.x + WINDOW_MARGIN,
      display.x + display.width - CHAT_WINDOW_WIDTH - WINDOW_MARGIN
    ),
    y: clampToRange(
      preferredY,
      display.y + WINDOW_MARGIN,
      display.y + display.height - CHAT_WINDOW_HEIGHT - WINDOW_MARGIN
    )
  };
}

function createChatWindow(): BrowserWindow {
  const position = chatHomePosition();
  const win = new BrowserWindow({
    width: CHAT_WINDOW_WIDTH,
    height: CHAT_WINDOW_HEIGHT,
    x: position.x,
    y: position.y,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js")
    }
  });

  win.loadFile(path.join(__dirname, "../renderer/chat.html"));
  attachWindowDiagnostics("chat", win);
  win.on("closed", () => {
    if (chatWindow === win) {
      chatWindow = null;
    }
  });
  return win;
}

function createPanelWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1160,
    height: 820,
    minWidth: 980,
    minHeight: 700,
    title: "Hoshino Agent Console",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js")
    }
  });
  win.loadFile(path.join(__dirname, "../renderer/panel.html"));
  attachWindowDiagnostics("panel", win);
  win.on("closed", () => {
    if (panelWindow === win) {
      panelWindow = null;
    }
  });
  return win;
}

function isUsableWindow(win: BrowserWindow | null): win is BrowserWindow {
  return !!win && !win.isDestroyed();
}

function ensurePanelWindow(): BrowserWindow {
  if (!isUsableWindow(panelWindow)) {
    panelWindow = createPanelWindow();
  }
  return panelWindow;
}

function ensurePetWindow(): BrowserWindow {
  if (!isUsableWindow(petWindow)) {
    petWindow = createPetWindow();
  }
  return petWindow;
}

function ensureChatWindow(): BrowserWindow {
  if (!isUsableWindow(chatWindow)) {
    chatWindow = createChatWindow();
  }
  return chatWindow;
}

function recoverChatWindow(): void {
  const win = ensureChatWindow();
  const position = chatHomePosition();
  win.setPosition(position.x, position.y, false);
  if (win.isMinimized()) {
    win.restore();
  }
  win.show();
  win.moveTop();
  win.focus();
}

function sendToWindow(win: BrowserWindow | null, channel: string, ...args: unknown[]): void {
  if (!isUsableWindow(win) || win.webContents.isDestroyed()) {
    return;
  }
  win.webContents.send(channel, ...args);
}

function openPanelWindow(focusTab?: string): void {
  const win = ensurePanelWindow();
  if (!win.isVisible()) {
    win.show();
  }
  if (win.isMinimized()) {
    win.restore();
  }
  win.focus();
  if (focusTab) {
    // Defer the message until the renderer is ready — webContents.send is
    // dropped silently if the page hasn't finished loading.
    if (win.webContents.isLoading()) {
      win.webContents.once("did-finish-load", () => {
        sendToWindow(panelWindow, "app:open-settings", focusTab);
      });
    } else {
      sendToWindow(win, "app:open-settings", focusTab);
    }
  }
}

function togglePanelWindow(): void {
  if (!isUsableWindow(panelWindow)) {
    panelWindow = createPanelWindow();
    panelWindow.show();
    panelWindow.focus();
    return;
  }
  if (panelWindow.isVisible()) {
    panelWindow.hide();
  } else {
    panelWindow.show();
    if (panelWindow.isMinimized()) {
      panelWindow.restore();
    }
    panelWindow.focus();
  }
}

app.on("second-instance", () => {
  if (isUsableWindow(panelWindow)) {
    openPanelWindow();
  }
});

// ---------------------------------------------------------------------------
// Backend HTTP plumbing
// ---------------------------------------------------------------------------

async function postJson(pathname: string, payload: unknown): Promise<unknown> {
  const response = await fetch(`${BACKEND_URL}${pathname}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`${pathname} failed: ${response.status}`);
  }
  return response.json();
}

async function putJson(pathname: string, payload: unknown): Promise<unknown> {
  const response = await fetch(`${BACKEND_URL}${pathname}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${pathname} failed: ${response.status} ${detail}`);
  }
  return response.json();
}

async function getJson(pathname: string): Promise<unknown> {
  const response = await fetch(`${BACKEND_URL}${pathname}`);
  if (!response.ok) {
    throw new Error(`${pathname} failed: ${response.status}`);
  }
  return response.json();
}

function absolutizeBackendUrl<T>(payload: T): T {
  if (!payload || typeof payload !== "object" || !("audio_url" in payload)) {
    return payload;
  }
  const record = payload as Record<string, unknown>;
  const audioUrl = record.audio_url;
  if (typeof audioUrl !== "string" || !audioUrl.startsWith("/")) {
    return payload;
  }
  return { ...record, audio_url: `${BACKEND_URL}${audioUrl}` } as T;
}

async function captureScreen(): Promise<void> {
  const response = await fetch(`${BACKEND_URL}/api/screen/capture`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Screen capture failed: ${response.status}`);
  }
  const artifact = (await response.json()) as { path?: string };
  if (artifact.path) {
    await shell.openPath(artifact.path);
  }
}

function setWatching(next: boolean): void {
  companionWatching = next;
  sendToWindow(petWindow, "pet:watching-changed", companionWatching);
}

// ---------------------------------------------------------------------------
// Pet context menu — built each time so labels reflect current state
// ---------------------------------------------------------------------------

async function buildContextMenu(): Promise<Menu> {
  let providers: ProviderListing | null = null;
  try {
    providers = (await getJson("/api/settings/providers")) as ProviderListing;
  } catch (error) {
    console.warn("provider list unavailable:", error);
  }

  const providerSubmenu: MenuItemConstructorOptions[] = providers
    ? providers.providers.map((p) => ({
        label: `${p.display_name}${p.configured ? "" : "  (未配置 API key)"}`,
        type: "radio" as const,
        checked: providers!.current === p.id,
        enabled: p.configured,
        click: async () => {
          try {
            await putJson("/api/settings", { provider: p.id });
          } catch (error) {
            console.error("Provider switch failed:", error);
          }
        }
      }))
    : [{ label: "(无法连接后端)", enabled: false }];

  const visionSubmenu: MenuItemConstructorOptions[] = providers
    ? providers.providers
        .filter((p) => p.supports_vision)
        .map((p) => ({
          label: `${p.display_name}${p.configured ? "" : "  (未配置)"}`,
          type: "radio" as const,
          checked: providers!.current_vision === p.id,
          enabled: p.configured,
          click: async () => {
            try {
              await putJson("/api/settings", { vision_provider: p.id });
            } catch (error) {
              console.error("Vision provider switch failed:", error);
            }
          }
        }))
    : [{ label: "(无法连接后端)", enabled: false }];

  const avatarSubmenu: MenuItemConstructorOptions[] = AVATAR_OPTIONS.map((option) => ({
    label: option.label,
    type: "radio" as const,
    checked: desktopPrefs.avatar === option.id,
    click: () => applyPrefsPatch({ avatar: option.id })
  }));

  const petVoiceSubmenu: MenuItemConstructorOptions[] = PET_VOICE_OPTIONS.map((option) => ({
    label: option.label,
    type: "radio" as const,
    checked: desktopPrefs.petVoice === option.id,
    click: () => applyPrefsPatch({ petVoice: option.id })
  }));

  const template: MenuItemConstructorOptions[] = [
    { label: "打开控制台", click: () => openPanelWindow() },
    { type: "separator" },
    {
      label: companionWatching ? "停止持续陪伴" : "开始持续陪伴",
      type: "checkbox",
      checked: companionWatching,
      click: () => setWatching(!companionWatching)
    },
    {
      label: "显示/找回输入框",
      click: () => recoverChatWindow()
    },
    {
      label: "允许桌宠语音回复",
      type: "checkbox",
      checked: desktopPrefs.voiceEnabled,
      click: () => applyPrefsPatch({ voiceEnabled: !desktopPrefs.voiceEnabled })
    },
    {
      label: "观察屏幕时朗读提醒",
      type: "checkbox",
      checked: desktopPrefs.observeSpeechEnabled,
      click: () => applyPrefsPatch({ observeSpeechEnabled: !desktopPrefs.observeSpeechEnabled })
    },
    { type: "separator" },
    { label: "切换形象 →", submenu: avatarSubmenu },
    { label: "切换音色 →", submenu: petVoiceSubmenu },
    { type: "separator" },
    { label: "切换对话模型 →", submenu: providerSubmenu },
    { label: "切换视觉模型 →", submenu: visionSubmenu },
    { type: "separator" },
    {
      label: "截屏",
      click: () => {
        captureScreen().catch((error) => console.error(error));
      }
    },
    { label: "设置...", click: () => openPanelWindow("pet") },
    { type: "separator" },
    { label: "退出应用", click: () => app.quit() }
  ];
  return Menu.buildFromTemplate(template);
}

async function showPetContextMenu(): Promise<void> {
  const menu = await buildContextMenu();
  menu.popup({ window: isUsableWindow(petWindow) ? petWindow : undefined });
}

// ---------------------------------------------------------------------------
// App boot
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  desktopPrefs = loadPrefs();
  petWindow = createPetWindow();
  chatWindow = createChatWindow();
  panelWindow = createPanelWindow();

  // ---- Backend proxies -----------------------------------------------------

  ipcMain.handle("agent:chat", async (_event, payload) => postJson("/api/chat", payload));
  ipcMain.handle("agent:observe", async (_event, payload) => postJson("/api/companion/observe", payload));
  ipcMain.handle("agent:tts", async (_event, payload) => {
    const response = await postJson("/api/voice/tts", payload);
    return absolutizeBackendUrl(response);
  });
  ipcMain.handle("agent:capabilities", async () => getJson("/api/capabilities"));
  ipcMain.handle("agent:profile", async (_event, sessionId: string) =>
    getJson(`/api/profile/${encodeURIComponent(sessionId)}`)
  );

  // ---- Backend settings ----------------------------------------------------

  ipcMain.handle("agent:settings:get", async () => getJson("/api/settings"));
  ipcMain.handle("agent:settings:update", async (_event, patch: AgentSettingsView) => {
    return putJson("/api/settings", patch);
  });
  ipcMain.handle("agent:settings:providers", async () => getJson("/api/settings/providers"));

  // ---- Desktop-local prefs -------------------------------------------------

  ipcMain.handle("app:prefs:get", async () => desktopPrefs);
  ipcMain.handle("app:prefs:update", async (_event, patch: Partial<DesktopPrefs>) =>
    applyPrefsPatch(patch)
  );
  ipcMain.handle("app:panel-history:record", async (_event, entry: PanelHistoryEntry) =>
    recordPanelHistoryEvent(entry)
  );
  ipcMain.handle("app:panel-history:list", async () => loadPanelHistoryEvents());

  // ---- Window controls -----------------------------------------------------

  ipcMain.on("pet:toggle-panel", () => togglePanelWindow());
  ipcMain.on("pet:show-context-menu", () => {
    showPetContextMenu().catch((error) => console.error("context menu error:", error));
  });
  ipcMain.on("pet:set-watching", (_event, watching: boolean) => setWatching(watching));
  ipcMain.on("chat:submit", (_event, text: string) => {
    const message = String(text || "").trim();
    if (!message) return;
    const win = ensurePetWindow();
    sendToWindow(chatWindow, "chat:busy-changed", true);
    if (win.webContents.isLoading()) {
      win.webContents.once("did-finish-load", () => {
        sendToWindow(win, "pet:chat-submit", message);
      });
    } else {
      sendToWindow(win, "pet:chat-submit", message);
    }
  });
  ipcMain.on("chat:busy", (_event, busy: boolean) => {
    sendToWindow(chatWindow, "chat:busy-changed", !!busy);
  });

  ipcMain.on("pet:drag-start", (_event, point: Point) => {
    if (!isUsableWindow(petWindow)) return;
    const [x, y] = petWindow.getPosition();
    petDragState = { startMouse: point, startWindow: { x, y } };
  });

  ipcMain.on("pet:drag-move", (_event, point: Point) => {
    if (!isUsableWindow(petWindow) || !petDragState) return;
    const nextX = petDragState.startWindow.x + point.x - petDragState.startMouse.x;
    const nextY = petDragState.startWindow.y + point.y - petDragState.startMouse.y;
    petWindow.setPosition(Math.round(nextX), Math.round(nextY), false);
  });

  ipcMain.on("pet:drag-end", () => {
    petDragState = null;
  });

  ipcMain.on("app:request-open-settings", (_event, tab?: string) => {
    openPanelWindow(tab);
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      petWindow = createPetWindow();
      chatWindow = createChatWindow();
      panelWindow = createPanelWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
