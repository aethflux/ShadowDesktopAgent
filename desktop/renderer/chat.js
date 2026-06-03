const form = document.getElementById("chatForm");
const input = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

const SCENE_STYLES = new Set(["sakura", "rainy", "neon"]);

function applySceneStyle(value, customUrl) {
  if (value === "custom" && customUrl) {
    document.body.dataset.scene = "custom";
    document.body.style.setProperty("--scene-bg", `url("${customUrl}")`);
    return;
  }
  document.body.style.removeProperty("--scene-bg");
  document.body.dataset.scene = SCENE_STYLES.has(value) ? value : "sakura";
}

// Busy watchdog: a turn normally clears busy via the pet window's
// "chat:busy-changed" signal. If that signal is ever lost (pet reload, dropped
// IPC, an escaped rejection), never leave the box permanently disabled.
let busyWatchdog = null;
const BUSY_MAX_MS = 75_000;

function setBusy(next) {
  input.disabled = next;
  sendBtn.disabled = next;
  sendBtn.textContent = next ? "处理中" : "发送";
  if (busyWatchdog) {
    clearTimeout(busyWatchdog);
    busyWatchdog = null;
  }
  if (next) {
    busyWatchdog = setTimeout(() => {
      busyWatchdog = null;
      setBusy(false);
    }, BUSY_MAX_MS);
  }
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
  // Enter sends the message. Skip while an IME (输入法) is composing so that
  // pressing Enter to confirm a Chinese candidate doesn't fire a premature
  // send. keyCode 229 is the legacy "IME is processing" signal.
  if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    form?.requestSubmit();
  }
});

// Esc hides the input box (bring it back via the pet's right-click menu →
// "显示/找回输入框"). Guard against IME so Esc can still cancel a candidate.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !event.isComposing) {
    event.preventDefault();
    window.shadow.hideChatInput?.();
  }
});

// --- Manual window drag --------------------------------------------------- //
// We intentionally do NOT use CSS `-webkit-app-region: drag`: on transparent,
// always-on-top, frameless windows it intermittently wedges mouse / move /
// close on Windows (the box "freezes" until it repaints). Driving the position
// over IPC — the same approach the pet window uses — is reliable and keeps the
// box movable even while a turn is in flight.
let dragStart = null;
const toPoint = (event) => ({ x: event.screenX, y: event.screenY });

form?.addEventListener("mousedown", (event) => {
  if (event.button !== 0) return;
  // Clicks on the field / send button must not start a drag.
  if (event.target.closest("input, button")) return;
  dragStart = toPoint(event);
  window.shadow.startChatDrag?.(dragStart);
  event.preventDefault();
});

window.addEventListener("mousemove", (event) => {
  if (!dragStart) return;
  window.shadow.moveChatDrag?.(toPoint(event));
});

window.addEventListener("mouseup", () => {
  if (!dragStart) return;
  dragStart = null;
  window.shadow.endChatDrag?.();
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
    .then((prefs) => applySceneStyle(prefs?.sceneStyle, prefs?.customSceneUrl))
    .catch((error) => console.warn("desktop prefs load failed:", error));
  window.shadow.onDesktopPrefsChanged?.((prefs) => applySceneStyle(prefs?.sceneStyle, prefs?.customSceneUrl));
  input.focus();
});
