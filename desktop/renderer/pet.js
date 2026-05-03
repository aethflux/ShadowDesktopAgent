/**
 * pet.js — Hoshino Desktop Pet
 *
 * Responsibilities:
 *   - Drag-and-drop window movement
 *   - Right-click / double-click menu
 *   - Continuous companion mode (periodic screen observation)
 *   - Cloud TTS playback with browser speech fallback
 *   - Engagement telemetry → passed to backend on every observe() call
 */

const petShell = document.getElementById("petShell");
const bubble = document.getElementById("petBubble");
const watchDot = document.getElementById("watchDot");

const BACKEND_URL = "http://127.0.0.1:8787";
const WATCH_SESSION_ID = "digital-twin-session";
const OBSERVE_INTERVAL_MS = 45_000;
const AVATAR_CLASSES = ["avatar-streamer", "avatar-swordswoman", "avatar-cyber"];
const defaultSettings = {
  avatar: "streamer",
  voice: "warm-girl",
  watching: false,
  voiceEnabled: true,
  observeSpeechEnabled: true
};
let desktopSettings = { ...defaultSettings };

function applyAvatar(value) {
  const selectedAvatar = ["streamer", "swordswoman", "cyber"].includes(value) ? value : "streamer";
  petShell?.classList.remove(...AVATAR_CLASSES);
  petShell?.classList.add(`avatar-${selectedAvatar}`);
}

function applyVoice(value) {
  desktopSettings.voice = ["warm-girl", "sweet-lady", "gentleman", "storyteller"].includes(value) ? value : "warm-girl";
}

function applySettings(next) {
  desktopSettings = { ...desktopSettings, ...next };
  applyVoice(desktopSettings.voice);
  applyAvatar(desktopSettings.avatar);
  setWatching(!!desktopSettings.watching, false);
}

// ---------------------------------------------------------------------------
// Engagement tracker — feeds idle / keypress / mouse data to the backend
// ---------------------------------------------------------------------------

const _eng = {
  lastKeyTime: Date.now(),
  lastMouseTime: Date.now(),
  keypressesThisMinute: 0,
  mouseMovesThisMinute: 0,
};

setInterval(() => { _eng.keypressesThisMinute = 0; }, 60_000);
setInterval(() => { _eng.mouseMovesThisMinute = 0; }, 60_000);

document.addEventListener("keydown", () => {
  _eng.keypressesThisMinute++;
  _eng.lastKeyTime = Date.now();
});
document.addEventListener("mousemove", () => {
  _eng.mouseMovesThisMinute++;
  _eng.lastMouseTime = Date.now();
});

function _isIdle() {
  return (Date.now() - Math.max(_eng.lastKeyTime, _eng.lastMouseTime)) > 5 * 60_000;
}

function _engagementState() {
  return {
    keypresses_last_minute: _eng.keypressesThisMinute,
    mouse_moves_last_minute: _eng.mouseMovesThisMinute,
    is_idle: _isIdle(),
  };
}

// ---------------------------------------------------------------------------
// Pet movement
// ---------------------------------------------------------------------------

let dragStart = null;
let didDrag = false;

function toPoint(event) {
  return { x: event.screenX, y: event.screenY };
}

function distanceFromStart(event) {
  if (!dragStart) return 0;
  return Math.hypot(event.screenX - dragStart.x, event.screenY - dragStart.y);
}

petShell?.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  if (event.target?.closest?.("[data-no-drag]")) return;
  event.preventDefault();
  dragStart = toPoint(event);
  didDrag = false;
  petShell.setPointerCapture(event.pointerId);
  window.bishoujo.startPetDrag(dragStart);
});

petShell?.addEventListener("pointermove", (event) => {
  if (!dragStart) return;
  if (distanceFromStart(event) > 4) didDrag = true;
  window.bishoujo.movePetDrag(toPoint(event));
});

petShell?.addEventListener("pointerup", (event) => {
  if (!dragStart) return;
  event.preventDefault();
  if (petShell.hasPointerCapture(event.pointerId)) {
    petShell.releasePointerCapture(event.pointerId);
  }
  window.bishoujo.endPetDrag();
  if (!didDrag) window.bishoujo.togglePanel();
  dragStart = null;
  didDrag = false;
});

petShell?.addEventListener("pointercancel", () => {
  window.bishoujo.endPetDrag();
  dragStart = null;
  didDrag = false;
});

// ---------------------------------------------------------------------------
// Bubble
// ---------------------------------------------------------------------------

let hideBubbleTimer = null;

function showBubble(text, sticky = false) {
  if (!bubble) return;
  bubble.textContent = text;
  bubble.classList.add("show");
  if (hideBubbleTimer) {
    clearTimeout(hideBubbleTimer);
    hideBubbleTimer = null;
  }
  if (!sticky) {
    hideBubbleTimer = setTimeout(() => bubble.classList.remove("show"), 9_000);
  }
}

// ---------------------------------------------------------------------------
// Speech — cloud audio first, browser speech synthesis as fallback
// ---------------------------------------------------------------------------

function setTalking(active) {
  petShell?.classList.toggle("talking", active);
}

let currentAudio = null;
let browserVoices = [];

function refreshBrowserVoices() {
  browserVoices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
}

if (window.speechSynthesis) {
  refreshBrowserVoices();
  window.speechSynthesis.onvoiceschanged = refreshBrowserVoices;
}

function preferredBrowserVoice() {
  if (!browserVoices.length) refreshBrowserVoices();
  const preferredNames = ["xiaoxiao", "huihui", "yaoyao", "yating", "tingting", "xiaoyi"];
  return (
    browserVoices.find((voice) => preferredNames.some((name) => voice.name.toLowerCase().includes(name))) ||
    browserVoices.find((voice) => voice.lang.toLowerCase().startsWith("zh-cn")) ||
    browserVoices.find((voice) => voice.lang.toLowerCase().startsWith("zh")) ||
    null
  );
}

function speak(text) {
  if (!text) return;
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  window.speechSynthesis.cancel();

  if (window.speechSynthesis) {
    const utter = new SpeechSynthesisUtterance(text);
    const voice = preferredBrowserVoice();
    if (voice) utter.voice = voice;
    utter.lang = "zh-CN";
    utter.rate = 1.0;
    utter.pitch = 1.08;
    utter.onstart = () => setTalking(true);
    utter.onend = () => setTalking(false);
    utter.onerror = () => setTalking(false);
    window.speechSynthesis.speak(utter);
  }
}

function _backendUrl(url) {
  if (!url) return "";
  if (url.startsWith("http") || url.startsWith("blob:") || url.startsWith("data:")) return url;
  return `${BACKEND_URL}${url}`;
}

function _playAudioUrl(url, fallbackText) {
  const src = _backendUrl(url);
  window.speechSynthesis.cancel();
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  const audio = new Audio(src);
  currentAudio = audio;
  audio.preload = "auto";
  audio.onplay = () => setTalking(true);
  audio.onended = () => {
    setTalking(false);
    if (currentAudio === audio) currentAudio = null;
  };
  audio.onerror = () => {
    setTalking(false);
    console.warn("[pet] cloud audio failed, falling back to browser speech", {
      src,
      error: audio.error ? audio.error.code : null
    });
    if (currentAudio === audio) currentAudio = null;
    speak(fallbackText);
  };
  console.log("[pet] playing cloud audio", src);
  audio.play().catch((error) => {
    console.warn("[pet] audio.play rejected, falling back to browser speech", error);
    if (currentAudio === audio) currentAudio = null;
    speak(fallbackText);
  });
}

async function say(text) {
  if (!text) return;
  if (!desktopSettings.voiceEnabled) return;
  const ttsResp = await window.bishoujo.tts({ text, voice: desktopSettings.voice });
  console.log("[pet] tts response", ttsResp);
  if (ttsResp.audio_url) {
    _playAudioUrl(ttsResp.audio_url, text);
  } else {
    speak(text);
  }
}

// ---------------------------------------------------------------------------
// Observation loop
// ---------------------------------------------------------------------------

let watching = false;
let observing = false;
let observeTimer = null;

async function observe(trigger = "interval") {
  if (observing) return;
  if (_isIdle() && trigger === "interval") return;
  observing = true;
  try {
    const eng = _engagementState();
    const response = await window.bishoujo.observe({
      session_id: WATCH_SESSION_ID,
      trigger,
      focus: trigger === "manual"
        ? "请看看当前屏幕，然后像我的数字分身搭档一样自然地和我说一句。"
        : null,
      ...eng,
    });

    const text = response.reply || "我在旁边陪着你。";
    const shouldSpeak = desktopSettings.observeSpeechEnabled
      && (trigger === "manual" || response.should_speak || response.significance === "high");
    if (shouldSpeak && text) {
      showBubble(text.slice(0, 86));
      await say(text);
    } else if (text) {
      showBubble(text.slice(0, 86));
    }
  } catch (error) {
    showBubble(`观察失败：${error && error.message ? error.message : String(error)}`);
  } finally {
    observing = false;
  }
}

// ---------------------------------------------------------------------------
// Companion mode
// ---------------------------------------------------------------------------

function setWatching(next, notifyMain = true) {
  watching = next;
  watchDot?.classList.toggle("active", watching);
  if (notifyMain) {
    window.bishoujo.setWatching(watching);
  }
  if (observeTimer) {
    clearInterval(observeTimer);
    observeTimer = null;
  }
  if (watching) {
    showBubble("持续陪伴已开启，我会像搭档一样偶尔看看屏幕。", true);
    observe("manual");
    observeTimer = setInterval(() => observe("interval"), OBSERVE_INTERVAL_MS);
  } else {
    showBubble("持续陪伴已暂停。");
  }
}

window.bishoujo.onWatchingChanged((next) => {
  if (next !== watching) setWatching(next, false);
});

window.bishoujo.onSettingsChanged((next) => {
  applySettings(next);
});

window.bishoujo.onAvatarChanged((avatar) => {
  applySettings({ avatar });
});

window.bishoujo.onVoiceChanged((voice) => {
  applySettings({ voice });
});

petShell?.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  window.bishoujo.showPetMenu();
});

petShell?.addEventListener("dblclick", (event) => {
  event.preventDefault();
  setWatching(!watching);
});

window.bishoujo.settings().then((settings) => {
  applySettings(settings);
}).catch((error) => {
  console.warn("[pet] failed to load settings", error);
  applySettings(defaultSettings);
});
