import { contextBridge, ipcRenderer } from "electron";

type Point = { x: number; y: number };

contextBridge.exposeInMainWorld("bishoujo", {
  chat: (payload: unknown) => ipcRenderer.invoke("agent:chat", payload),
  observe: (payload: unknown) => ipcRenderer.invoke("agent:observe", payload),
  tts: (payload: unknown) => ipcRenderer.invoke("agent:tts", payload),
  profile: (sessionId: string) => ipcRenderer.invoke("agent:profile", sessionId),
  capabilities: () => ipcRenderer.invoke("agent:capabilities"),
  togglePanel: () => ipcRenderer.send("pet:toggle-panel"),
  showPetMenu: () => ipcRenderer.send("pet:show-context-menu"),
  setWatching: (watching: boolean) => ipcRenderer.send("pet:set-watching", watching),
  onWatchingChanged: (handler: (watching: boolean) => void) => {
    ipcRenderer.on("pet:watching-changed", (_event, watching: boolean) => handler(watching));
  },
  startPetDrag: (point: Point) => ipcRenderer.send("pet:drag-start", point),
  movePetDrag: (point: Point) => ipcRenderer.send("pet:drag-move", point),
  endPetDrag: () => ipcRenderer.send("pet:drag-end")
});

declare global {
  interface Window {
    bishoujo: {
      chat: (payload: unknown) => Promise<any>;
      observe: (payload: unknown) => Promise<any>;
      tts: (payload: unknown) => Promise<any>;
      profile: (sessionId: string) => Promise<any>;
      capabilities: () => Promise<any>;
      togglePanel: () => void;
      showPetMenu: () => void;
      setWatching: (watching: boolean) => void;
      onWatchingChanged: (handler: (watching: boolean) => void) => void;
      startPetDrag: (point: Point) => void;
      movePetDrag: (point: Point) => void;
      endPetDrag: () => void;
    };
  }
}
