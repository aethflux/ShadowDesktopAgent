import { app, BrowserWindow, ipcMain, Menu, screen, shell } from "electron";
import path from "node:path";

const BACKEND_URL = process.env.BISHOUJO_AGENT_API ?? "http://127.0.0.1:8787";

type Point = { x: number; y: number };
type DragState = { startMouse: Point; startWindow: Point };
type DesktopSettings = {
  avatar: string;
  voice: string;
  watching: boolean;
  voiceEnabled: boolean;
  observeSpeechEnabled: boolean;
};

let petWindow: BrowserWindow | null = null;
let panelWindow: BrowserWindow | null = null;
let petDragState: DragState | null = null;
let companionWatching = false;
let desktopSettings: DesktopSettings = {
  avatar: "streamer",
  voice: "warm-girl",
  watching: false,
  voiceEnabled: true,
  observeSpeechEnabled: true
};

app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
}

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
  win.webContents.on("preload-error", (_event, path, error) => {
    console.error(`[${name}] preload-error path=${path} error=${error}`);
  });
}

function createPetWindow(): BrowserWindow {
  const display = screen.getPrimaryDisplay().workAreaSize;
  const width = 170;
  const height = 260;
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

function broadcastSettings(): void {
  petWindow?.webContents.send("app:settings-changed", desktopSettings);
  panelWindow?.webContents.send("app:settings-changed", desktopSettings);
}

function openPanelWindow(): void {
  if (!panelWindow) {
    panelWindow = createPanelWindow();
    return;
  }
  if (!panelWindow.isVisible()) {
    panelWindow.show();
  }
  panelWindow.focus();
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

async function captureScreen(): Promise<void> {
  const response = await fetch(`${BACKEND_URL}/api/screen/capture`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Screen capture failed: ${response.status}`);
  }
  const artifact = await response.json() as { path?: string };
  if (artifact.path) {
    await shell.openPath(artifact.path);
  }
}

function setWatching(next: boolean): void {
  companionWatching = next;
  desktopSettings = { ...desktopSettings, watching: next };
  petWindow?.webContents.send("pet:watching-changed", companionWatching);
  broadcastSettings();
}

function showPetContextMenu(): void {
  const menu = Menu.buildFromTemplate([
    { label: "打开控制台", click: () => openPanelWindow() },
    {
      label: companionWatching ? "停止持续陪伴" : "开始持续陪伴",
      click: () => setWatching(!companionWatching)
    },
    {
      label: "截屏",
      click: () => {
        captureScreen().catch((error) => console.error(error));
      }
    },
    { type: "separator" },
    { label: "退出应用", click: () => app.quit() }
  ]);
  menu.popup({ window: petWindow ?? undefined });
}

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

function absolutizeBackendUrl<T>(payload: T): T {
  if (!payload || typeof payload !== "object" || !("audio_url" in payload)) {
    return payload;
  }

  const record = payload as Record<string, unknown>;
  const audioUrl = record.audio_url;
  if (typeof audioUrl !== "string" || !audioUrl.startsWith("/")) {
    return payload;
  }

  return {
    ...record,
    audio_url: `${BACKEND_URL}${audioUrl}`
  } as T;
}

app.whenReady().then(() => {
  petWindow = createPetWindow();
  panelWindow = createPanelWindow();

  ipcMain.handle("agent:chat", async (_event, payload) => postJson("/api/chat", payload));
  ipcMain.handle("agent:observe", async (_event, payload) => postJson("/api/companion/observe", payload));
  ipcMain.handle("agent:tts", async (_event, payload) => {
    const response = await postJson("/api/voice/tts", payload);
    return absolutizeBackendUrl(response);
  });

  ipcMain.handle("agent:capabilities", async () => {
    const response = await fetch(`${BACKEND_URL}/api/capabilities`);
    if (!response.ok) {
      throw new Error(`Capabilities API failed: ${response.status}`);
    }
    return response.json();
  });

  ipcMain.handle("agent:profile", async (_event, sessionId: string) => {
    const response = await fetch(`${BACKEND_URL}/api/profile/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      throw new Error(`Profile API failed: ${response.status}`);
    }
    return response.json();
  });
  ipcMain.handle("agent:settings", async () => desktopSettings);
  ipcMain.handle("agent:update-settings", async (_event, patch: Partial<DesktopSettings>) => {
    desktopSettings = {
      ...desktopSettings,
      ...patch,
      watching: patch.watching ?? desktopSettings.watching
    };
    companionWatching = desktopSettings.watching;
    petWindow?.webContents.send("pet:watching-changed", companionWatching);
    petWindow?.webContents.send("pet:avatar-changed", desktopSettings.avatar);
    petWindow?.webContents.send("pet:voice-changed", desktopSettings.voice);
    broadcastSettings();
    return desktopSettings;
  });

  ipcMain.on("pet:toggle-panel", () => togglePanelWindow());
  ipcMain.on("pet:show-context-menu", () => showPetContextMenu());
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

  ipcMain.on("pet:set-avatar", (_event, avatar: string) => {
    desktopSettings = { ...desktopSettings, avatar };
    petWindow?.webContents.send("pet:avatar-changed", avatar);
    broadcastSettings();
  });

  ipcMain.on("pet:set-voice", (_event, voice: string) => {
    desktopSettings = { ...desktopSettings, voice };
    petWindow?.webContents.send("pet:voice-changed", voice);
    broadcastSettings();
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
