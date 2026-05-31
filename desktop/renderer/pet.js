/**
 * pet.js — Shadow Desktop Pet
 *
 * Responsibilities:
 *   - Drag-and-drop window movement
 *   - Right-click / double-click menu, mini chat
 *   - Continuous companion mode (periodic screen observation)
 *   - Cloud TTS playback with browser speech fallback
 *   - Engagement telemetry → backend on every observe()
 *   - Agent state machine that drives expressions/animations:
 *       idle | thinking | working | talking | watching | listening | error | happy
 */

const petShell = document.getElementById("petShell");
const bubble = document.getElementById("petBubble");
const watchDot = document.getElementById("watchDot");
const avatarImage = document.getElementById("avatarImage");

const BACKEND_URL = "http://127.0.0.1:8787";
const COMPANION_SESSION_ID = "pet-companion-session";
const COMPANION_SESSION_TITLE = "桌宠陪伴";
const COMPANION_HISTORY_RETENTION_MS = 5 * 60_000;
const OBSERVE_INTERVAL_MS = 45_000;
const AVATAR_ASSETS = {
  streamer: "./assets/avatars/shadow-streamer.png",
  swordswoman: "./assets/avatars/shadow-swordswoman.png",
  cyber: "./assets/avatars/shadow-cyber.png",
  senpai: "./assets/avatars/shadow-senpai.png",
};
const AVATAR_OPTIONS = Object.keys(AVATAR_ASSETS);
const AVATAR_CLASSES = AVATAR_OPTIONS.map((name) => `avatar-${name}`);
const PET_VOICE_OPTIONS = ["warm-girl", "sweet-lady", "gentleman", "storyteller"];
const SCENE_STYLE_OPTIONS = ["sakura", "rainy", "neon"];

let selectedAvatar = "streamer";
let selectedVoice = "warm-girl";
let desktopPrefs = {
  avatar: selectedAvatar,
  sceneStyle: "sakura",
  customSceneUrl: "",
  customAvatarUrl: "",
  petVoice: selectedVoice,
  voiceEnabled: true,
  observeSpeechEnabled: true,
};
let userTurnActive = false;
let suppressObserveUntil = 0;
let observeRunVersion = 0;

function applyAvatar(value) {
  // "custom" = an AI-generated portrait whose absolute URL is in prefs.
  const custom = value === "custom" && desktopPrefs.customAvatarUrl;
  selectedAvatar = custom
    ? "custom"
    : AVATAR_OPTIONS.includes(value)
      ? value
      : "streamer";
  petShell?.classList.remove(...AVATAR_CLASSES, "avatar-custom");
  petShell?.classList.add(`avatar-${selectedAvatar}`);
  if (avatarImage) {
    // Guard against a missing/renamed sprite leaving a broken-image icon on
    // the transparent desktop window. Fall back to the default sprite once;
    // if even that fails, hide the <img> so only the aura/effects remain.
    avatarImage.dataset.fallback = "";
    avatarImage.onerror = () => {
      if (avatarImage.dataset.fallback === "1") {
        avatarImage.style.visibility = "hidden";
        return;
      }
      avatarImage.dataset.fallback = "1";
      avatarImage.src = AVATAR_ASSETS.streamer;
    };
    avatarImage.style.visibility = "";
    avatarImage.src = custom ? desktopPrefs.customAvatarUrl : AVATAR_ASSETS[selectedAvatar];
    avatarImage.alt = `${selectedAvatar} avatar`;
  }
}

function applyVoice(value) {
  selectedVoice = PET_VOICE_OPTIONS.includes(value) ? value : "warm-girl";
}

function applySceneStyle(value) {
  if (value === "custom" && desktopPrefs.customSceneUrl) {
    document.body.dataset.scene = "custom";
    if (petShell) petShell.dataset.scene = "custom";
    document.body.style.setProperty("--scene-bg", `url("${desktopPrefs.customSceneUrl}")`);
    return;
  }
  document.body.style.removeProperty("--scene-bg");
  const sceneStyle = SCENE_STYLE_OPTIONS.includes(value) ? value : "sakura";
  document.body.dataset.scene = sceneStyle;
  if (petShell) petShell.dataset.scene = sceneStyle;
}

applyAvatar(selectedAvatar);
applyVoice(selectedVoice);
applySceneStyle(desktopPrefs.sceneStyle);

async function loadDesktopPrefs() {
  try {
    desktopPrefs = { ...desktopPrefs, ...(await window.shadow.desktopPrefs()) };
    applyAvatar(desktopPrefs.avatar);
    applyVoice(desktopPrefs.petVoice);
    applySceneStyle(desktopPrefs.sceneStyle);
  } catch (error) {
    console.warn("[pet] failed to load desktop prefs", error);
  }
}

window.shadow.onDesktopPrefsChanged?.((prefs) => {
  desktopPrefs = { ...desktopPrefs, ...prefs };
  applyAvatar(desktopPrefs.avatar);
  applyVoice(desktopPrefs.petVoice);
  applySceneStyle(desktopPrefs.sceneStyle);
  if (!desktopPrefs.voiceEnabled || (!desktopPrefs.observeSpeechEnabled && currentSpeechSource === "observe")) {
    stopSpeech();
  }
});

// ---------------------------------------------------------------------------
// Agent state machine
// ---------------------------------------------------------------------------
//
// `data-state` on the pet shell drives all CSS expression rules. We layer
// states on top of the long-lived "watching" mode: when a transient state
// (thinking/working/talking/error/happy) finishes, we fall back to either
// "watching" or "idle" depending on whether continuous companion mode is on.

const STATE = {
  IDLE: "idle",
  THINKING: "thinking",
  WORKING: "working",
  TALKING: "talking",
  WATCHING: "watching",
  LISTENING: "listening",
  ERROR: "error",
  HAPPY: "happy",
};

let watching = false;
let stateRevertTimer = null;
let stickyState = null;     // when set, no auto-revert until cleared

function baselineState() {
  return watching ? STATE.WATCHING : STATE.IDLE;
}

function setState(next, { sticky = false, revertAfter = null } = {}) {
  if (stateRevertTimer) {
    clearTimeout(stateRevertTimer);
    stateRevertTimer = null;
  }
  petShell.dataset.state = next;
  stickyState = sticky ? next : null;
  if (!sticky && next !== STATE.IDLE && next !== STATE.WATCHING) {
    const ms = revertAfter ?? 3500;
    stateRevertTimer = setTimeout(() => {
      // If a sticky state took over while we waited, respect that.
      if (stickyState) return;
      petShell.dataset.state = baselineState();
    }, ms);
  }
}

function clearStickyState() {
  stickyState = null;
  if (stateRevertTimer) clearTimeout(stateRevertTimer);
  petShell.dataset.state = baselineState();
}

// ---------------------------------------------------------------------------
// Engagement tracker
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
  window.shadow.startPetDrag(dragStart);
});

petShell?.addEventListener("pointermove", (event) => {
  if (!dragStart) return;
  if (distanceFromStart(event) > 4) didDrag = true;
  window.shadow.movePetDrag(toPoint(event));
});

petShell?.addEventListener("pointerup", (event) => {
  if (!dragStart) return;
  event.preventDefault();
  if (petShell.hasPointerCapture(event.pointerId)) {
    petShell.releasePointerCapture(event.pointerId);
  }
  window.shadow.endPetDrag();
  if (!didDrag) window.shadow.togglePanel();
  dragStart = null;
  didDrag = false;
});

petShell?.addEventListener("pointercancel", () => {
  window.shadow.endPetDrag();
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

function recordCompanionHistory(role, text, meta = "") {
  if (!text) return;
  window.shadow.recordPanelHistory?.({
    sessionId: COMPANION_SESSION_ID,
    title: COMPANION_SESSION_TITLE,
    fixed: true,
    role,
    text,
    meta,
    ts: Date.now(),
    retentionMs: COMPANION_HISTORY_RETENTION_MS,
  }).catch((error) => console.warn("[pet] failed to record panel history", error));
}

// ---------------------------------------------------------------------------
// Speech — cloud audio first, browser speech synthesis as fallback
// ---------------------------------------------------------------------------

function setTalking(active) {
  petShell?.classList.toggle("talking", active);
  if (active) {
    setState(STATE.TALKING, { sticky: true });
  } else if (stickyState === STATE.TALKING) {
    clearStickyState();
  }
}

let currentAudio = null;
let currentSpeechSource = null;
let browserVoices = [];

function canSpeak(source = "chat") {
  return desktopPrefs.voiceEnabled && (source !== "observe" || desktopPrefs.observeSpeechEnabled);
}

function stopSpeech() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.currentTime = 0;
    currentAudio = null;
  }
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  currentSpeechSource = null;
  setTalking(false);
}

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

function speechText(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[([^\]\n]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/https?:\/\/\S+/gi, "链接")
    .replace(/[\r\n]+/g, " ")
    .replace(/[\p{P}\p{S}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function speak(text, source = "chat") {
  const spoken = speechText(text);
  if (!spoken) return;
  if (!canSpeak(source)) {
    stopSpeech();
    return;
  }
  stopSpeech();

  if (window.speechSynthesis) {
    currentSpeechSource = source;
    const utter = new SpeechSynthesisUtterance(spoken);
    const voice = preferredBrowserVoice();
    if (voice) utter.voice = voice;
    utter.lang = "zh-CN";
    utter.rate = 1.0;
    utter.pitch = 1.08;
    utter.onstart = () => setTalking(true);
    utter.onend = () => {
      currentSpeechSource = null;
      setTalking(false);
    };
    utter.onerror = () => {
      currentSpeechSource = null;
      setTalking(false);
    };
    window.speechSynthesis.speak(utter);
  }
}

function _backendUrl(url) {
  if (!url) return "";
  if (url.startsWith("http") || url.startsWith("blob:") || url.startsWith("data:")) return url;
  return `${BACKEND_URL}${url}`;
}

function _playAudioUrl(url, fallbackText, source = "chat") {
  if (!canSpeak(source)) {
    stopSpeech();
    return;
  }
  const src = _backendUrl(url);
  stopSpeech();
  const audio = new Audio(src);
  currentAudio = audio;
  currentSpeechSource = source;
  audio.preload = "auto";
  audio.onplay = () => setTalking(true);
  audio.onended = () => {
    setTalking(false);
    if (currentAudio === audio) {
      currentAudio = null;
      currentSpeechSource = null;
    }
  };
  audio.onerror = () => {
    setTalking(false);
    console.warn("[pet] cloud audio failed, falling back to browser speech", {
      src,
      error: audio.error ? audio.error.code : null,
    });
    if (currentAudio === audio) currentAudio = null;
    if (canSpeak(source)) speak(fallbackText, source);
  };
  console.log("[pet] playing cloud audio", src);
  audio.play().catch((error) => {
    console.warn("[pet] audio.play rejected, falling back to browser speech", error);
    if (currentAudio === audio) currentAudio = null;
    if (canSpeak(source)) speak(fallbackText, source);
  });
}

async function say(text, { source = "chat" } = {}) {
  const spoken = speechText(text);
  if (!spoken) return;
  if (!canSpeak(source)) {
    stopSpeech();
    return;
  }
  const ttsResp = await window.shadow.tts({ text: spoken, voice: selectedVoice });
  if (!canSpeak(source)) {
    stopSpeech();
    return;
  }
  console.log("[pet] tts response", ttsResp);
  if (ttsResp.audio_url) {
    _playAudioUrl(ttsResp.audio_url, spoken, source);
  } else {
    speak(spoken, source);
  }
}

// ---------------------------------------------------------------------------
// Observation loop
// ---------------------------------------------------------------------------

let observing = false;
let observeTimer = null;

function shouldSkipObservation(trigger) {
  return userTurnActive || (trigger === "interval" && Date.now() < suppressObserveUntil);
}

async function observe(trigger = "interval") {
  if (observing) return;
  if (_isIdle() && trigger === "interval") return;
  if (shouldSkipObservation(trigger)) return;
  observing = true;
  const runVersion = ++observeRunVersion;
  // Watching is already sticky once enabled — no transient state change.
  try {
    const eng = _engagementState();
    const response = await window.shadow.observe({
      session_id: COMPANION_SESSION_ID,
      trigger,
      focus: trigger === "manual"
        ? "请看看当前屏幕，然后像我的数字分身搭档一样自然地和我说一句。"
        : null,
      ...eng,
    });

    if (runVersion !== observeRunVersion || userTurnActive || Date.now() < suppressObserveUntil) {
      return;
    }
    const text = response.reply || "我在旁边陪着你。";
    const shouldSpeak = (
      desktopPrefs.voiceEnabled &&
      desktopPrefs.observeSpeechEnabled &&
      (trigger === "manual" || response.should_speak || response.significance === "high")
    );
    if (text) {
      recordCompanionHistory(
        "assistant",
        text,
        `持续陪伴 · ${trigger} · ${response.significance || "medium"}`
      );
    }
    if (shouldSpeak && text) {
      showBubble(text.slice(0, 86));
      await say(text, { source: "observe" });
    }
  } catch (error) {
    setState(STATE.ERROR, { revertAfter: 2400 });
    showBubble(`观察失败：${error && error.message ? error.message : String(error)}`);
  } finally {
    observing = false;
  }
}

async function handlePetChat(text) {
  text = String(text || "").trim();
  if (!text) return;
  if (userTurnActive) {
    window.shadow.setPetChatBusy?.(false);
    return;
  }

  userTurnActive = true;
  window.shadow.setPetChatBusy?.(true);
  suppressObserveUntil = Date.now() + 60_000;
  observeRunVersion++;
  recordCompanionHistory("user", text, "桌宠输入");

  setState(STATE.THINKING, { sticky: true });
  showBubble("我在想...");

  try {
    const response = await window.shadow.chat({
      message: text,
      session_id: COMPANION_SESSION_ID,
      attachments: [],
    });
    const reply = response.reply || "我在。";
    const meta = `${response.trace?.active_agent || "agent"} | 桌宠输入`;
    recordCompanionHistory("assistant", reply, meta);

    // If the agent invoked tools, briefly show the working sparkle state
    // before transitioning to the spoken reply.
    const usedTools = (response.trace?.tool_calls || []).length > 0;
    if (usedTools) {
      setState(STATE.WORKING, { sticky: true });
      await new Promise((r) => setTimeout(r, 700));
    }
    // Quick happy beat then talking.
    setState(STATE.HAPPY, { sticky: true });
    await new Promise((r) => setTimeout(r, 350));

    showBubble(reply.slice(0, 86));
    await say(reply, { source: "chat" }); // setTalking inside say() will set TALKING sticky
  } catch (error) {
    setState(STATE.ERROR, { revertAfter: 2800 });
    const message = error && error.message ? error.message : String(error);
    showBubble(`请求失败：${message}`, true);
    recordCompanionHistory("assistant", `请求失败：${message}`, "桌宠输入 · error");
  } finally {
    userTurnActive = false;
    window.shadow.setPetChatBusy?.(false);
    suppressObserveUntil = Date.now() + 20_000;
  }
}

window.shadow.onPetChatSubmit?.((text) => {
  handlePetChat(text).catch((error) => {
    window.shadow.setPetChatBusy?.(false);
    setState(STATE.ERROR, { revertAfter: 2800 });
    showBubble(`请求失败：${error && error.message ? error.message : String(error)}`, true);
  });
});

// ---------------------------------------------------------------------------
// Companion mode
// ---------------------------------------------------------------------------

function setWatching(next) {
  watching = next;
  watchDot?.classList.toggle("active", watching);
  window.shadow.setWatching(watching);
  if (observeTimer) {
    clearInterval(observeTimer);
    observeTimer = null;
  }
  if (watching) {
    setState(STATE.WATCHING, { sticky: true });
    showBubble("持续陪伴已开启，我会像搭档一样偶尔看看屏幕。", true);
    observe("manual");
    observeTimer = setInterval(() => observe("interval"), OBSERVE_INTERVAL_MS);
  } else {
    clearStickyState();
    showBubble("持续陪伴已暂停。");
  }
}

window.shadow.onWatchingChanged((next) => {
  if (next !== watching) setWatching(next);
});

petShell?.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  window.shadow.showPetMenu();
});

petShell?.addEventListener("dblclick", (event) => {
  event.preventDefault();
  setWatching(!watching);
});

// ---------------------------------------------------------------------------
// Idle entry — explicit so the pet starts in a known state
// ---------------------------------------------------------------------------

loadDesktopPrefs().then(() => {
  if (!desktopPrefs.voiceEnabled) {
    stopSpeech();
  }
});
setState(STATE.IDLE);
