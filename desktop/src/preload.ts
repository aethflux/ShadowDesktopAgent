import { contextBridge, ipcRenderer } from "electron";

type Point = { x: number; y: number };

/** Backend-stored agent settings (mirrors ``app.schemas.SettingsView``). */
type AgentSettings = Record<string, unknown>;

/** Window-local UX preferences (Electron-side only — not synced to backend). */
type DesktopPrefs = {
  avatar: string;
  voiceEnabled: boolean;
  observeSpeechEnabled: boolean;
};

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

contextBridge.exposeInMainWorld("bishoujo", {
  // Chat / observation / TTS — proxy to backend.
  chat: (payload: unknown) => ipcRenderer.invoke("agent:chat", payload),
  observe: (payload: unknown) => ipcRenderer.invoke("agent:observe", payload),
  tts: (payload: unknown) => ipcRenderer.invoke("agent:tts", payload),
  profile: (sessionId: string) => ipcRenderer.invoke("agent:profile", sessionId),
  capabilities: () => ipcRenderer.invoke("agent:capabilities"),

  // Backend-stored agent settings (provider / voice engine / memory etc).
  agentSettings: () => ipcRenderer.invoke("agent:settings:get") as Promise<AgentSettings>,
  updateAgentSettings: (patch: Partial<AgentSettings>) =>
    ipcRenderer.invoke("agent:settings:update", patch) as Promise<AgentSettings>,
  listProviders: () => ipcRenderer.invoke("agent:settings:providers") as Promise<ProviderListing>,

  // Window-local UX prefs (avatar choice, mute toggles).
  desktopPrefs: () => ipcRenderer.invoke("app:prefs:get") as Promise<DesktopPrefs>,
  updateDesktopPrefs: (patch: Partial<DesktopPrefs>) =>
    ipcRenderer.invoke("app:prefs:update", patch) as Promise<DesktopPrefs>,
  onDesktopPrefsChanged: (handler: (prefs: DesktopPrefs) => void) => {
    ipcRenderer.on("app:prefs-changed", (_event, prefs: DesktopPrefs) => handler(prefs));
  },

  // Pet window controls.
  togglePanel: () => ipcRenderer.send("pet:toggle-panel"),
  showPetMenu: () => ipcRenderer.send("pet:show-context-menu"),
  setWatching: (watching: boolean) => ipcRenderer.send("pet:set-watching", watching),
  onWatchingChanged: (handler: (watching: boolean) => void) => {
    ipcRenderer.on("pet:watching-changed", (_event, watching: boolean) => handler(watching));
  },
  startPetDrag: (point: Point) => ipcRenderer.send("pet:drag-start", point),
  movePetDrag: (point: Point) => ipcRenderer.send("pet:drag-move", point),
  endPetDrag: () => ipcRenderer.send("pet:drag-end"),

  // Cross-window settings deep-link: pet's right-click menu can ask the
  // panel to open the settings modal at a particular tab.
  onOpenSettings: (handler: (tab?: string) => void) => {
    ipcRenderer.on("app:open-settings", (_event, tab?: string) => handler(tab));
  },
  requestOpenSettings: (tab?: string) => ipcRenderer.send("app:request-open-settings", tab),
});

declare global {
  interface Window {
    bishoujo: {
      chat: (payload: unknown) => Promise<any>;
      observe: (payload: unknown) => Promise<any>;
      tts: (payload: unknown) => Promise<any>;
      profile: (sessionId: string) => Promise<any>;
      capabilities: () => Promise<any>;
      agentSettings: () => Promise<AgentSettings>;
      updateAgentSettings: (patch: Partial<AgentSettings>) => Promise<AgentSettings>;
      listProviders: () => Promise<ProviderListing>;
      desktopPrefs: () => Promise<DesktopPrefs>;
      updateDesktopPrefs: (patch: Partial<DesktopPrefs>) => Promise<DesktopPrefs>;
      onDesktopPrefsChanged: (handler: (prefs: DesktopPrefs) => void) => void;
      togglePanel: () => void;
      showPetMenu: () => void;
      setWatching: (watching: boolean) => void;
      onWatchingChanged: (handler: (watching: boolean) => void) => void;
      startPetDrag: (point: Point) => void;
      movePetDrag: (point: Point) => void;
      endPetDrag: () => void;
      onOpenSettings: (handler: (tab?: string) => void) => void;
      requestOpenSettings: (tab?: string) => void;
    };
  }
}
