import { app, BrowserWindow, ipcMain, Menu, screen, shell, MenuItemConstructorOptions } from "electron";
import path from "node:path";
import fs from "node:fs";

const BACKEND_URL = process.env.BISHOUJO_AGENT_API ?? "http://127.0.0.1:8787";

type Point = { x: number; y: number };
type DragState = { startMouse: Point; startWindow: Point };

/** Window-local UX preferences. Persisted to ``userData/desktop-prefs.json``. */
type DesktopPrefs = {
  avatar: string;
  voiceEnabled: boolean;
  observeSpeechEnabled: boolean;
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
  voiceEnabled: true,
  observeSpeechEnabled: true
};

let petWindow: BrowserWindow | null = null;
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
  petWindow?.webContents.send("app:prefs-changed", desktopPrefs);
  panelWindow?.webContents.send("app:prefs-changed", desktopPrefs);
  return desktopPrefs;
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
  const height = 430;
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
  return win;
}

function openPanelWindow(focusTab?: string): void {
  if (!panelWindow) {
    panelWindow = createPanelWindow();
  }
  if (!panelWindow.isVisible()) {
    panelWindow.show();
  }
  panelWindow.focus();
  if (focusTab) {
    // Defer the message until the renderer is ready — webContents.send is
    // dropped silently if the page hasn't finished loading.
    if (panelWindow.webContents.isLoading()) {
      panelWindow.webContents.once("did-finish-load", () => {
        panelWindow?.webContents.send("app:open-settings", focusTab);
      });
    } else {
      panelWindow.webContents.send("app:open-settings", focusTab);
    }
  }
}

function togglePanelWindow(): void {
  if (!panelWindow) {
    panelWindow = createPanelWindow();
    return;
  }
  if (panelWindow.isVisible()) {
    panelWindow.hide();
  } else {
    panelWindow.show();
    panelWindow.focus();
  }
}

app.on("second-instance", () => {
  if (panelWindow) {
    if (!panelWindow.isVisible()) {
      panelWindow.show();
    }
    if (panelWindow.isMinimized()) {
      panelWindow.restore();
    }
    panelWindow.focus();
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
  petWindow?.webContents.send("pet:watching-changed", companionWatching);
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
      label: "桌宠开口说话",
      type: "checkbox",
      checked: desktopPrefs.voiceEnabled,
      click: () => applyPrefsPatch({ voiceEnabled: !desktopPrefs.voiceEnabled })
    },
    {
      label: "观察时也朗读",
      type: "checkbox",
      checked: desktopPrefs.observeSpeechEnabled,
      click: () => applyPrefsPatch({ observeSpeechEnabled: !desktopPrefs.observeSpeechEnabled })
    },
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
    { label: "设置...", click: () => openPanelWindow("general") },
    { type: "separator" },
    { label: "退出应用", click: () => app.quit() }
  ];
  return Menu.buildFromTemplate(template);
}

async function showPetContextMenu(): Promise<void> {
  const menu = await buildContextMenu();
  menu.popup({ window: petWindow ?? undefined });
}

// ---------------------------------------------------------------------------
// App boot
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  desktopPrefs = loadPrefs();
  petWindow = createPetWindow();
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

  // ---- Window controls -----------------------------------------------------

  ipcMain.on("pet:toggle-panel", () => togglePanelWindow());
  ipcMain.on("pet:show-context-menu", () => {
    showPetContextMenu().catch((error) => console.error("context menu error:", error));
  });
  ipcMain.on("pet:set-watching", (_event, watching: boolean) => setWatching(watching));

  ipcMain.on("pet:drag-start", (_event, point: Point) => {
    if (!petWindow) return;
    const [x, y] = petWindow.getPosition();
    petDragState = { startMouse: point, startWindow: { x, y } };
  });

  ipcMain.on("pet:drag-move", (_event, point: Point) => {
    if (!petWindow || !petDragState) return;
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
      panelWindow = createPanelWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
