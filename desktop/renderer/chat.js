const form = document.getElementById("chatForm");
const input = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

const SCENE_STYLES = new Set(["sakura", "rainy", "neon"]);

function applySceneStyle(value) {
  document.body.dataset.scene = SCENE_STYLES.has(value) ? value : "sakura";
}

function setBusy(next) {
  input.disabled = next;
  sendBtn.disabled = next;
  sendBtn.textContent = next ? "处理中" : "发送";
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || input.disabled) return;
  input.value = "";
  setBusy(true);
  window.shadow.submitPetChat(text);
});

input?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
  }
});

window.shadow.onPetChatBusyChanged?.((busy) => {
  setBusy(!!busy);
  if (!busy) {
    input.focus();
  }
});

window.addEventListener("DOMContentLoaded", () => {
  applySceneStyle("sakura");
  window.shadow.desktopPrefs?.()
    .then((prefs) => applySceneStyle(prefs?.sceneStyle))
    .catch((error) => console.warn("desktop prefs load failed:", error));
  window.shadow.onDesktopPrefsChanged?.((prefs) => applySceneStyle(prefs?.sceneStyle));
  input.focus();
});
