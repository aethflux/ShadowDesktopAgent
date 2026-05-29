"""Persona-fidelity benchmark (LLM-as-judge).

Unlike ``tests/test_persona.py`` (which only checks that the *prompt string*
is assembled correctly), this measures whether the model **actually adopts**
the persona: given each preset's system prompt and a neutral probe question,
does the reply match that persona's tone, address term, catchphrases, emoji
and length preferences?

For every preset × probe we make two LLM calls:
  1. an *answer* call using the persona's rendered system prompt, and
  2. a *judge* call that scores the answer 0–5 against the persona spec.

This REQUIRES a working LLM (network + API key) and costs tokens, so it is
NOT part of the offline test suite or CI. Run it by hand after changing the
persona templates, the default model, or temperature:

    python -m eval.run_persona_eval
    python -m eval.run_persona_eval --min-score 3.5   # gate on average
    python -m eval.run_persona_eval --show-answers     # print replies
    python -m eval.run_persona_eval --repeats 3        # average 3 samples/probe

Robustness: model and judge calls retry on transient failures and on
unparseable judge replies, so a flaky JSON response no longer silently drops
a sample. ``--repeats`` further smooths variance by averaging N samples per
(persona, probe) at linear token cost.

Exit codes:
    0 — average score meets the floor (or no floor set)
    1 — below floor, or no persona produced a usable answer
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import settings
from app.schemas import PersonaConfig, PersonaPreset
from app.services.model_client import ModelClient
from app.services.persona import builder


def load_probes() -> list[dict]:
    path = Path(__file__).parent / "persona_probes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["probes"]


def _extract_json_object(raw: str) -> dict | None:
    """Pull the first balanced ``{...}`` out of a model reply (tolerates
    ```json fences and trailing prose). Mirrors the agent's plan parser."""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _chat_text_with_retry(
    client: ModelClient,
    messages: list[dict],
    *,
    retries: int = 2,
) -> str:
    """Call the model and return non-empty text, retrying transient failures.

    Network blips and empty completions are the main causes of dropped samples
    in the benchmark; a couple of backed-off retries make the run far more
    reproducible without masking a genuinely broken provider.
    """
    last_err = "empty reply"
    for attempt in range(retries + 1):
        try:
            response = await client.chat(messages, tools=None)
            text = client.extract_text(response).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001 — benchmark, surface as retry
            last_err = str(exc)
        if attempt < retries:
            await asyncio.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"chat failed after {retries + 1} attempt(s): {last_err}")


async def answer_as_persona(client: ModelClient, system_prompt: str, question: str) -> str:
    return await _chat_text_with_retry(
        client,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )


_JUDGE_SYSTEM = (
    "你是一个严格、客观的角色扮演风格评审。你只评估回答的"
    "【风格是否贴合给定人设】，不评估事实正确性或有用性。"
    "重点看：语气/性格、对用户的称呼、说话风格、口头禅的使用、"
    "emoji 用量、回复长度是否都与人设一致。"
    "只返回 JSON：{\"score\": 0-5 的整数, \"reason\": \"一句话理由\"}。"
    "0=完全不像，3=部分贴合，5=高度贴合。只返回 JSON 本体。"
)


async def judge_fidelity(
    client: ModelClient,
    persona: PersonaConfig,
    question: str,
    answer: str,
    *,
    retries: int = 2,
) -> tuple[int | None, str]:
    spec = (
        f"名字：{persona.name}\n"
        f"性格特质：{'、'.join(persona.personality_traits)}\n"
        f"说话风格：{persona.speaking_style}\n"
        f"对用户的称呼：{persona.address_user_as}\n"
        f"口头禅：{'、'.join(persona.catchphrases) or '（无）'}\n"
        f"emoji 用量：{persona.emoji_usage}\n"
        f"回复长度偏好：{persona.response_length}"
    )
    user_prompt = (
        f"【人设规格】\n{spec}\n\n"
        f"【用户问题】\n{question}\n\n"
        f"【被评回答】\n{answer}\n\n"
        "这段回答有多符合上述人设的风格？给出 score 和 reason。"
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    # Retry on unparseable / non-numeric judge replies — the single most common
    # cause of dropped samples (e.g. ARIA's n=2). The model usually returns
    # clean JSON on a second try; only give up after ``retries`` attempts.
    last_reason = "no judge reply"
    for attempt in range(retries + 1):
        try:
            text = await _chat_text_with_retry(client, messages, retries=0)
        except Exception as exc:  # noqa: BLE001
            last_reason = f"judge call failed: {exc}"
        else:
            parsed = _extract_json_object(text)
            if parsed and "score" in parsed:
                try:
                    score = int(parsed["score"])
                except (TypeError, ValueError):
                    last_reason = f"non-numeric score: {parsed.get('score')!r}"
                else:
                    return max(0, min(5, score)), str(parsed.get("reason", "")).strip()
            else:
                last_reason = f"unparseable judge reply: {text[:80]}"
        if attempt < retries:
            await asyncio.sleep(0.6 * (attempt + 1))
    return None, last_reason


async def run(min_score: float, show_answers: bool, repeats: int = 1) -> int:
    probes = load_probes()
    presets: list[PersonaPreset] = builder.list_presets()
    # Answer with the chat model; judge with the same model for simplicity.
    client = ModelClient()

    # Preflight: make sure an LLM is actually reachable before burning a full
    # matrix of calls on connection errors.
    try:
        await answer_as_persona(client, "你是一个助手。", "回复“ok”即可。")
    except Exception as exc:
        print(f"FAIL: LLM not reachable — configure a provider/API key first.\n  {exc}")
        return 1

    original = settings.persona_config_json
    per_persona: dict[str, list[int]] = {}
    transcript: list[tuple[str, str, int | None, str, str]] = []

    try:
        for preset in presets:
            # Activate this persona, then render the companion system prompt
            # exactly as the live agent would.
            settings.persona_config_json = preset.config.model_dump_json()
            system_prompt = builder.render("companion-agent")
            scores: list[int] = []
            for probe in probes:
                question = probe["message"]
                # Repeat each probe ``repeats`` times and keep every score, so
                # the per-persona average smooths out single-sample variance.
                for _rep in range(max(1, repeats)):
                    try:
                        answer = await answer_as_persona(client, system_prompt, question)
                    except Exception as exc:
                        transcript.append((preset.id, probe["id"], None, f"answer error: {exc}", ""))
                        continue
                    score, reason = await judge_fidelity(client, preset.config, question, answer)
                    if score is not None:
                        scores.append(score)
                    transcript.append((preset.id, probe["id"], score, reason, answer))
            per_persona[preset.id] = scores
    finally:
        settings.persona_config_json = original

    # ---- Report -------------------------------------------------------- #
    print("Persona-fidelity benchmark (LLM-as-judge, 0–5)\n")
    all_scores: list[int] = []
    for preset in presets:
        scores = per_persona.get(preset.id, [])
        all_scores.extend(scores)
        avg = sum(scores) / len(scores) if scores else 0.0
        bar = "█" * round(avg) + "·" * (5 - round(avg))
        print(f"  {preset.label:<18} [{bar}] {avg:.2f}  (n={len(scores)})")

    if show_answers:
        print("\n--- transcripts ---")
        for pid, probe_id, score, reason, answer in transcript:
            print(f"\n[{pid} / {probe_id}] score={score}  {reason}")
            print(f"  {answer[:200]}")

    if not all_scores:
        print("\nFAIL: no usable scores (judge could not parse any reply).")
        return 1

    overall = sum(all_scores) / len(all_scores)
    print(f"\nOverall fidelity: {overall:.2f}/5  over {len(all_scores)} samples")

    if min_score > 0 and overall < min_score:
        print(f"FAIL: below floor {min_score:.2f}")
        return 1
    print("OK.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="Optional gate on the overall average (0 = report-only).")
    parser.add_argument("--show-answers", action="store_true",
                        help="Print the generated replies and judge reasons.")
    parser.add_argument("--repeats", type=int, default=1,
                        help="Samples per (persona, probe) — higher smooths variance "
                             "at linear token cost. Default 1.")
    args = parser.parse_args()
    return asyncio.run(run(args.min_score, args.show_answers, args.repeats))


if __name__ == "__main__":
    sys.exit(main())
