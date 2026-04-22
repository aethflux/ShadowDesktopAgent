from __future__ import annotations

from typing import Any

from app.schemas import AgentTrace, IntentMatch


class PlannerAgent:
    valid_agents = {"companion-agent", "desktop-agent", "terminal-agent"}
    valid_intents = {
        "conversation",
        "screen_observation",
        "continuous_companion",
        "terminal",
        "coding",
        "memory_profile",
        "persona",
        "voice",
        "unknown",
    }

    def merge_intents(self, local_intent: IntentMatch, model_plan: dict[str, Any] | None) -> AgentTrace:
        model_intent = self._parse_model_intent(model_plan)
        if not model_intent:
            return self._to_trace(local_intent, "model unavailable")

        should_use_model = (
            model_intent.confidence >= 0.72
            and model_intent.delegated_to in self.valid_agents
            and model_intent.intent in self.valid_intents
        )
        should_keep_local = local_intent.confidence >= 0.72 and model_intent.confidence < 0.86

        if should_use_model and not should_keep_local:
            merged = model_intent
            source = "model intent override"
        else:
            merged = local_intent
            source = "local intent retained"

        disagreement = ""
        if model_intent.intent != local_intent.intent:
            disagreement = (
                f" Disagreement: local={local_intent.intent}/{local_intent.confidence:.2f}, "
                f"model={model_intent.intent}/{model_intent.confidence:.2f}."
            )

        return self._to_trace(merged, source + disagreement)

    def _parse_model_intent(self, model_plan: dict[str, Any] | None) -> IntentMatch | None:
        if not model_plan:
            return None
        try:
            confidence = float(model_plan.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        delegated_to = model_plan.get("delegated_to") or "companion-agent"
        if delegated_to not in self.valid_agents:
            delegated_to = "companion-agent"

        intent = model_plan.get("intent") or "unknown"
        if intent not in self.valid_intents:
            intent = "unknown"

        tool_candidates = model_plan.get("tool_candidates") or []
        if not isinstance(tool_candidates, list):
            tool_candidates = []

        return IntentMatch(
            intent=intent,
            delegated_to=delegated_to,
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=str(model_plan.get("reasoning") or "Model intent classification."),
            signals=["model"],
            tool_candidates=[str(item) for item in tool_candidates[:6]],
        )

    @staticmethod
    def _to_trace(intent: IntentMatch, source: str) -> AgentTrace:
        tools = ""
        if intent.tool_candidates:
            tools = f" Tool candidates: {', '.join(intent.tool_candidates)}."
        return AgentTrace(
            active_agent="planner",
            delegated_to=intent.delegated_to,
            reasoning=(
                f"Intent={intent.intent}, confidence={intent.confidence:.2f}, source={source}. "
                f"{intent.reasoning}{tools}"
            ),
        )
