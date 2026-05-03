import { contextBridge, ipcRenderer } from "electron";

type Point = { x: number; y: number };
type DesktopSettings = {
  avatar: string;
  voice: string;
  watching: boolean;
  voiceEnabled: boolean;
  observeSpeechEnabled: boolean;
};

contextBridge.exposeInMainWorld("bishoujo", {
  chat: (payload: unknown) => ipcRenderer.invoke("agent:chat", payload),
  observe: (payload: unknown) => ipcRenderer.invoke("agent:observe", payload),
  tts: (payload: unknown) => ipcRenderer.invoke("agent:tts", payload),
  profile: (sessionId: string) => ipcRenderer.invoke("agent:profile", sessionId),
  capabilities: () => ipcRenderer.invoke("agent:capabilities"),
  settings: () => ipcRenderer.invoke("agent:settings"),
  updateSettings: (patch: Partial<DesktopSettings>) => ipcRenderer.invoke("agent:update-settings", patch),
  togglePanel: () => ipcRenderer.send("pet:toggle-panel"),
  showPetMenu: () => ipcRenderer.send("pet:show-context-menu"),
  setWatching: (watching: boolean) => ipcRenderer.send("pet:set-watching", watching),
  onSettingsChanged: (handler: (settings: DesktopSettings) => void) => {
    ipcRenderer.on("app:settings-changed", (_event, settings: DesktopSettings) => handler(settings));
  },
  onWatchingChanged: (handler: (watching: boolean) => void) => {
    ipcRenderer.on("pet:watching-changed", (_event, watching: boolean) => handler(watching));
  },
  onAvatarChanged: (handler: (avatar: string) => void) => {
    ipcRenderer.on("pet:avatar-changed", (_event, avatar: string) => handler(avatar));
  },
  onVoiceChanged: (handler: (voice: string) => void) => {
    ipcRenderer.on("pet:voice-changed", (_event, voice: string) => handler(voice));
  },
  startPetDrag: (point: Point) => ipcRenderer.send("pet:drag-start", point),
  movePetDrag: (point: Point) => ipcRenderer.send("pet:drag-move", point),
  endPetDrag: () => ipcRenderer.send("pet:drag-end"),
  setAvatar: (avatar: string) => ipcRenderer.send("pet:set-avatar", avatar),
  setVoice: (voice: string) => ipcRenderer.send("pet:set-voice", voice)
});

declare global {
  interface Window {
    bishoujo: {
      chat: (payload: unknown) => Promise<any>;
      observe: (payload: unknown) => Promise<any>;
      tts: (payload: unknown) => Promise<any>;
      profile: (sessionId: string) => Promise<any>;
      capabilities: () => Promise<any>;
      settings: () => Promise<DesktopSettings>;
      updateSettings: (patch: Partial<DesktopSettings>) => Promise<DesktopSettings>;
      togglePanel: () => void;
      showPetMenu: () => void;
      setWatching: (watching: boolean) => void;
      onSettingsChanged: (handler: (settings: DesktopSettings) => void) => void;
      onWatchingChanged: (handler: (watching: boolean) => void) => void;
      onAvatarChanged: (handler: (avatar: string) => void) => void;
      onVoiceChanged: (handler: (voice: string) => void) => void;
      startPetDrag: (point: Point) => void;
      movePetDrag: (point: Point) => void;
      endPetDrag: () => void;
      setAvatar: (avatar: string) => void;
      setVoice: (voice: string) => void;
    };
  }
}
