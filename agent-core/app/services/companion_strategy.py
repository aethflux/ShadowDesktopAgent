"""Active companion strategy: focus detection and smart nudge generation.

Tracks user engagement over time and decides when Shadow should proactively
speak up — offering encouragement when focus is dipping, celebrating when
things are going well, or gracefully going quiet during deep work.

Core concepts
-------------
**Engagement score** (0.0 – 1.0): A smoothed composite of:
  - keypress rate (keystrokes / minute, normalized to 0–1 over a 0–120 range)
  - mouse move rate (moves / minute, normalized 0–1 over 0–80)
  - last interaction recency (1.0 if < 30 s ago, decaying linearly to 0.4 at 30 min)

**Nudge policy** — triggered when all of:
  1. Engagement has been below the ``low_engagement_threshold`` for > 2 consecutive observations
  2. More than ``min_interval_seconds`` have passed since the last nudge
  3. The user is not in "idle" state (is_idle=True means they stepped away)

**Celebration policy** — triggered when:
  1. Engagement has been above ``high_engagement_threshold`` for > 3 consecutive observations
  2. More than 5 minutes since last celebration

**Quiet mode** — triggered when is_idle=True, suppresses all proactive speech.

Design notes
------------
- All state is kept in-memory per session (no extra storage needed).
- Designed to be called before every screen observation; the orchestrator
  gets back either a nudge string or None (stay silent).
- Sentiment analysis of the last user message is used to adjust nudge tone
  (cheerful / neutral / gentle-warning).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.schemas import ObservationRequest


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

LOW_ENGAGEMENT_THRESHOLD  = 0.30   # below this → potentially struggling
HIGH_ENGAGEMENT_THRESHOLD = 0.72   # above this → flow state, celebrate
IDLE_THRESHOLD           = 0.10   # effectively no input

KEYPRESSES_PER_MINUTE_CEIL = 120   # 2 per second is a fast coder
MOUSE_MOVES_PER_MINUTE_CEIL = 80

MIN_NUDGE_INTERVAL_SEC   = 180     # at least 3 min between nudges
MIN_CELEBRATION_INTERVAL_SEC = 300  # 5 min between celebrations

DEEP_WORK_PATIENCE       = 3       # consecutive low-eng observations before nudge


# --------------------------------------------------------------------------- #
#  State
# --------------------------------------------------------------------------- #

@dataclass
class EngagementSnapshot:
    score: float
    keypress_rate: float
    mouse_rate: float
    recency_score: float
    ts: float = field(default_factory=time.time)


@dataclass
class SessionState:
    history: list[EngagementSnapshot] = field(default_factory=list)
    consecutive_low: int = 0
    consecutive_high: int = 0
    last_nudge_ts: float = 0.0
    last_celebration_ts: float = 0.0
    last_interaction_ts: float = time.time()
    last_user_sentiment: str = "neutral"  # positive / neutral / negative
    last_user_message: str = ""

    def prune_history(self, max_age: float = 600.0) -> None:
        now = time.time()
        self.history = [h for h in self.history if now - h.ts < max_age]


# --------------------------------------------------------------------------- #
#  Sentiment (keyword-based, zero-dependency)
# --------------------------------------------------------------------------- #

_POSITIVE = {"好", "棒", "厉害", "成功", "完成", "对了", "可以", "不错", "开心",
             "谢谢", "great", "good", "nice", "awesome", "perfect", "thanks", "wow", "cool"}
_NEGATIVE = {"累", "困", "烦", "崩溃", "不会", "不懂", "难", "烦", "糟",
             "tired", "frustrated", "stuck", "confused", "hard", "annoying", "sucks", "ugh"}


def _classify_sentiment(message: str) -> str:
    if not message:
        return "neutral"
    lowered = message.lower()
    pos = sum(1 for w in _POSITIVE if w in lowered)
    neg = sum(1 for w in _NEGATIVE if w in lowered)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


# --------------------------------------------------------------------------- #
#  Engagement scorer
# --------------------------------------------------------------------------- #

def _rate(value: float, ceil: float) -> float:
    return min(1.0, max(0.0, value / ceil))


def compute_engagement(req: ObservationRequest, last_ts: float | None) -> float:
    """Compute a 0–1 engagement score from an observation request."""
    now = time.time()
    if last_ts is None:
        last_ts = now

    kpm = float(req.keypresses_last_minute)
    mpm = float(req.mouse_moves_last_minute)

    key_score  = _rate(kpm, KEYPRESSES_PER_MINUTE_CEIL)
    mouse_score = _rate(mpm, MOUSE_MOVES_PER_MINUTE_CEIL)

    # Recency: 1.0 if < 30 s, decaying to 0.4 at 30 min.
    elapsed = now - last_ts
    if elapsed < 30:
        recency = 1.0
    elif elapsed > 1800:
        recency = 0.4
    else:
        recency = 1.0 - 0.6 * ((elapsed - 30) / 1770)

    # Weighted average: keystrokes matter more for "real work".
    return 0.55 * key_score + 0.25 * mouse_score + 0.20 * recency


# --------------------------------------------------------------------------- #
#  CompanionStrategy
# --------------------------------------------------------------------------- #

class CompanionStrategy:
    """Decides when and what Shadow should say proactively."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def _state(self, session_id: str) -> SessionState:
        return self._sessions.setdefault(session_id, SessionState())

    # ---- Public API ------------------------------------------------------ #

    def should_speak(self, request: ObservationRequest) -> bool:
        """Returns True if Shadow should proactively comment this turn."""
        state = self._state(request.session_id)
        state.prune_history()

        engagement = compute_engagement(request, state.last_interaction_ts)
        snapshot = EngagementSnapshot(
            score=engagement,
            keypress_rate=float(request.keypresses_last_minute),
            mouse_rate=float(request.mouse_moves_last_minute),
            recency_score=1.0 if not request.is_idle else 0.0,
        )
        state.history.append(snapshot)
        state.last_interaction_ts = time.time()

        # Quiet mode: user is idle (stepped away).
        if request.is_idle:
            return False

        now = time.time()

        # Celebration: sustained high engagement.
        if engagement >= HIGH_ENGAGEMENT_THRESHOLD:
            state.consecutive_high += 1
            state.consecutive_low = 0
            if state.consecutive_high >= 3 and (now - state.last_celebration_ts) > MIN_CELEBRATION_INTERVAL_SEC:
                state.last_celebration_ts = now
                state.consecutive_high = 0
                return True
        else:
            state.consecutive_high = 0

        # Nudge: sustained low engagement.
        if engagement < LOW_ENGAGEMENT_THRESHOLD:
            state.consecutive_low += 1
            if state.consecutive_low >= DEEP_WORK_PATIENCE and (now - state.last_nudge_ts) > MIN_NUDGE_INTERVAL_SEC:
                state.last_nudge_ts = now
                state.consecutive_low = 0
                return True
        else:
            state.consecutive_low = 0

        return False

    def decide_nudge(self, request: ObservationRequest) -> str | None:
        """Return the text of the nudge, or None to stay silent."""
        if not self.should_speak(request):
            return None

        state = self._state(request.session_id)
        sentiment = _classify_sentiment(state.last_user_message)
        latest = state.history[-1] if state.history else None

        # Choose tone based on sentiment history and engagement level.
        if sentiment == "negative" or (latest and latest.score < 0.20):
            # Struggling → gentle encouragement.
            options = [
                "感觉你有点卡住了，要不起来动一动？我相信你能搞定的！",
                "别急，慢慢来，实在不行我陪你聊两句换换脑子。",
                "要不要休息一下？磨刀不误砍柴工 ☕",
            ]
        elif sentiment == "positive" and latest and latest.score >= HIGH_ENGAGEMENT_THRESHOLD:
            # Flow state → celebrate and step back.
            options = [
                "看你状态超好，继续加油！我在旁边安静陪着你 💪",
                "太厉害了，保持这个节奏！我先去角落里待会儿。",
                None,  # silent celebration — don't interrupt flow
            ]
        else:
            # Normal low-engagement nudge.
            options = [
                "我注意到你有一会儿没动静了，还好吗？",
                "别忘了喝水和动一动，身体也很重要哦 🌿",
                "需要我帮你查点什么吗？还是只是想安静写代码？",
            ]
            # Filter out None during flow.
            options = [o for o in options if o is not None]

        import random
        return random.choice(options)

    def record_message(self, session_id: str, message: str) -> None:
        """Call this after every user message to update sentiment tracking."""
        state = self._state(session_id)
        state.last_user_message = message
        state.last_user_sentiment = _classify_sentiment(message)
        state.last_interaction_ts = time.time()

    def get_state(self, session_id: str) -> dict:
        """Return current engagement metrics for debugging/monitoring."""
        state = self._state(session_id)
        latest = state.history[-1] if state.history else None
        return {
            "session_id": session_id,
            "engagement_score": round(latest.score, 3) if latest else 0.0,
            "keypresses_per_min": round(latest.keypress_rate, 1) if latest else 0,
            "mouse_moves_per_min": round(latest.mouse_rate, 1) if latest else 0,
            "consecutive_low": state.consecutive_low,
            "consecutive_high": state.consecutive_high,
            "last_sentiment": state.last_user_sentiment,
            "nudges_sent": state.consecutive_low,
        }
