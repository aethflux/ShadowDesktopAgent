/**
 * pet.js — Hoshino Desktop Pet
 *
 * Responsibilities:
 *   - Drag-and-drop window movement
 *   - Right-click / double-click menu
 *   - Continuous companion mode (periodic screen observation)
 *   - Speech synthesis (browser Web Speech API)
 *   - Engagement telemetry → passed to backend on every observe() call
 *   - Live2D model rendering (when Cubism 5 SDK is loaded)
 *
 * Integration points:
 *   - Live2D: loaded as ES module, canvas overlays CSS avatar
 *   - Voice input: handled by browser speech recognition in the desktop UI
 */

import { createLive2DAdapter } from 'live2d_adapter';

const petShell = document.getElementById("petShell");
const bubble = document.getElementById("petBubble");
const watchDot = document.getElementById("watchDot");
const live2dCanvas = document.getElementById("live2d-canvas");

const WATCH_SESSION_ID = "digital-twin-session";
const OBSERVE_INTERVAL_MS = 45_000;

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
  if (petShell.hasPointerCapture(event.pointerId)) petShell.releasePointerCapture(event.pointerId);
  window.bishoujo.endPetDrag();
  if (!didDrag) window.bishoujo.togglePanel();
  dragStart = null; didDrag = false;
});
petShell?.addEventListener("pointercancel", () => {
  window.bishoujo.endPetDrag();
  dragStart = null; didDrag = false;
});

// ---------------------------------------------------------------------------
// Bubble
// ---------------------------------------------------------------------------

let hideBubbleTimer = null;
function showBubble(text, sticky = false) {
  if (!bubble) return;
  bubble.textContent = text;
  bubble.classList.add("show");
  if (hideBubbleTimer) { clearTimeout(hideBubbleTimer); hideBubbleTimer = null; }
  if (!sticky) hideBubbleTimer = setTimeout(() => bubble.classList.remove("show"), 9_000);
}

// ---------------------------------------------------------------------------
// Live2D adapter (starts after DOM is ready)
// ---------------------------------------------------------------------------

let live2dAdapter = null;
let live2dReady = false;

async function initLive2D() {
  try {
    live2dAdapter = createLive2DAdapter(live2dCanvas);
    await live2dAdapter.load('/live2d/Haru/');
    live2dReady = true;
    // Hide CSS avatar, show Live2D canvas
    document.getElementById("petBody")?.style.setProperty("display", "none");
    live2dCanvas.style.display = "block";
    console.info("[Pet] Live2D Haru model loaded successfully");
  } catch (err) {
    console.warn("[Pet] Live2D failed to load, using CSS avatar:", err);
    live2dReady = false;
  }
}

// Start loading Live2D asynchronously (non-blocking)
initLive2D();

// ---------------------------------------------------------------------------
// Speech — browser speech synthesis by default
// ---------------------------------------------------------------------------

function speak(text) {
  if (!text) return;
  window.speechSynthesis.cancel();

  if (window.speechSynthesis) {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "zh-CN";
    utter.rate = 1.0;
    utter.pitch = 1.08;
    utter.onstart = () => {
      petShell?.classList.toggle("talking", true);
      if (live2dReady && live2dAdapter) live2dAdapter.startSpeaking();
    };
    utter.onend = () => {
      petShell?.classList.toggle("talking", false);
      if (live2dReady && live2dAdapter) live2dAdapter.stopSpeaking();
    };
    utter.onerror = () => {
      petShell?.classList.toggle("talking", false);
      if (live2dReady && live2dAdapter) live2dAdapter.stopSpeaking();
    };
    window.speechSynthesis.speak(utter);
  }
}

/** Play a local audio file through a transient <audio> element. */
function _playAudioUrl(url) {
  const audio = new Audio(url);
  audio.onplay = () => {
    petShell?.classList.toggle("talking", true);
    if (live2dReady && live2dAdapter) live2dAdapter.startSpeaking();
  };
  audio.onended = () => {
    petShell?.classList.toggle("talking", false);
    if (live2dReady && live2dAdapter) live2dAdapter.stopSpeaking();
  };
  audio.onerror = () => {
    petShell?.classList.toggle("talking", false);
    if (live2dReady && live2dAdapter) live2dAdapter.stopSpeaking();
    speak(url);
  };
  audio.play().catch(() => speak(url));
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
    const shouldSpeak = trigger === "manual" || response.should_speak || response.significance === "high";
    if (shouldSpeak && text) {
      showBubble(text.slice(0, 86));
      const ttsResp = await window.bishoujo.tts({ text });
      if (ttsResp.audio_url) {
        _playAudioUrl(ttsResp.audio_url);
      } else {
        speak(text);
      }
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

function setWatching(next) {
  watching = next;
  watchDot?.classList.toggle("active", watching);
  window.bishoujo.setWatching(watching);
  if (observeTimer) { clearInterval(observeTimer); observeTimer = null; }
  if (watching) {
    showBubble("持续陪伴已开启，我会像搭档一样偶尔看看屏幕。", true);
    observe("manual");
    observeTimer = setInterval(() => observe("interval"), OBSERVE_INTERVAL_MS);
  } else {
    showBubble("持续陪伴已暂停。");
  }
}

window.bishoujo.onWatchingChanged((next) => {
  if (next !== watching) setWatching(next);
});

petShell?.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  window.bishoujo.showPetMenu();
});
petShell?.addEventListener("dblclick", (event) => {
  event.preventDefault();
  setWatching(!watching);
});

// ---------------------------------------------------------------------------
// Live2D motion triggers from companion events
// ---------------------------------------------------------------------------

/** Called by the backend companion logic to trigger a nudge/expression. */
window._triggerLive2DMotion = (motion) => {
  if (live2dReady && live2dAdapter) {
    if (motion === "Happy" || motion === "happy") {
      live2dAdapter.playMotion("Idle"); // fallback
    } else if (motion === "Sad" || motion === "sad") {
      live2dAdapter.setExpression("F03"); // F03 is a sad expression in Haru
    } else if (motion === "Angry") {
      live2dAdapter.setExpression("F05"); // F05 is Angry
    } else if (motion === "Surprise") {
      live2dAdapter.setExpression("F06"); // F06 is Surprise
    } else {
      live2dAdapter.playMotion(motion || "Idle");
    }
  }
};
