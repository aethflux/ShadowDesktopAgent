"""Offline evaluation for the local intent router.

Runs ``RouterAgent.classify_local`` over a curated test set and reports both
intent-level and delegate-level accuracy. Designed to run in CI (no network,
no LLM calls) so it can gate merges on router quality.

Usage::

    python -m eval.run_intent_eval
    python -m eval.run_intent_eval --min-accuracy 0.85

Exit codes:
    0  — all thresholds met
    1  — below the required accuracy floor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agents.router import RouterAgent


def load_testset(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score(router: RouterAgent, cases: list[dict]) -> tuple[int, int, list[tuple[dict, str, str]]]:
    """Return (intent_hits, delegate_hits, failures) over ``cases``."""
    intent_hits = 0
    delegate_hits = 0
    failures: list[tuple[dict, str, str]] = []
    for case in cases:
        result = router.classify_local(case["message"])
        intent_ok = result.intent == case["expected_intent"]
        delegate_ok = result.delegated_to == case["expected_delegate"]
        if intent_ok:
            intent_hits += 1
        if delegate_ok:
            delegate_hits += 1
        if not (intent_ok and delegate_ok):
            failures.append((case, result.intent, result.delegated_to))
    return intent_hits, delegate_hits, failures


def _print_failures(failures: list[tuple[dict, str, str]]) -> None:
    for case, got_intent, got_delegate in failures:
        trap = f"  [{case['trap']}]" if case.get("trap") else ""
        print(
            f"  - '{case['message'][:46]}' "
            f"exp {case['expected_intent']}/{case['expected_delegate']}, "
            f"got {got_intent}/{got_delegate}{trap}"
        )


def evaluate(min_accuracy: float, challenge_floor: float) -> int:
    router = RouterAgent()
    base = Path(__file__).parent

    # ---- Core set: gates CI. Must stay clean. --------------------------- #
    core = load_testset(base / "intent_testset.json")
    intent_hits, delegate_hits, failures = _score(router, core)
    total = len(core)
    delegate_acc = delegate_hits / total

    print(f"Intent router eval — CORE — {total} cases")
    print(f"  intent accuracy   : {intent_hits}/{total} = {intent_hits / total:.1%}")
    print(f"  delegate accuracy : {delegate_hits}/{total} = {delegate_acc:.1%}")
    if failures:
        print("\n  Core failures:")
        _print_failures(failures)

    # ---- Challenge set: informational. Surfaces the true edge. ---------- #
    challenge_path = base / "intent_challenge_set.json"
    challenge_delegate_acc = 1.0
    if challenge_path.exists():
        challenge = load_testset(challenge_path)
        c_intent, c_delegate, c_failures = _score(router, challenge)
        c_total = len(challenge)
        challenge_delegate_acc = c_delegate / c_total
        print(f"\nIntent router eval — CHALLENGE — {c_total} hard cases")
        print(f"  intent accuracy   : {c_intent}/{c_total} = {c_intent / c_total:.1%}")
        print(f"  delegate accuracy : {c_delegate}/{c_total} = {challenge_delegate_acc:.1%}")
        if c_failures:
            print("\n  Challenge misses (these map the router's real boundary):")
            _print_failures(c_failures)

    # ---- Follow-up set: dialogue-state routing (sticky fallback). -------- #
    # These messages carry no keyword signal at all; correct routing depends
    # on which agent handled the previous turn. The stateless baseline shows
    # what the router did before sticky resolution existed.
    followup_path = base / "intent_followup_set.json"
    followup_acc = 1.0
    if followup_path.exists():
        followups = load_testset(followup_path)
        sticky_hits = 0
        stateless_hits = 0
        f_failures: list[tuple[dict, str]] = []
        for case in followups:
            local = router.classify_local(case["message"])
            delegated, _source = RouterAgent.resolve_delegate(
                local, case.get("previous_delegate")
            )
            if delegated == case["expected_delegate"]:
                sticky_hits += 1
            else:
                f_failures.append((case, delegated))
            if local.delegated_to == case["expected_delegate"]:
                stateless_hits += 1
        f_total = len(followups)
        followup_acc = sticky_hits / f_total
        print(f"\nIntent router eval — FOLLOW-UP — {f_total} dialogue-state cases")
        print(f"  stateless baseline : {stateless_hits}/{f_total} = {stateless_hits / f_total:.1%}")
        print(f"  sticky resolution  : {sticky_hits}/{f_total} = {followup_acc:.1%}")
        if f_failures:
            print("\n  Follow-up misses:")
            for case, got in f_failures:
                print(
                    f"  - '{case['message'][:46]}' (prev={case.get('previous_delegate')}) "
                    f"exp {case['expected_delegate']}, got {got}  [{case.get('note', '')}]"
                )

    # ---- Gate decision -------------------------------------------------- #
    exit_code = 0
    if delegate_acc < min_accuracy:
        print(f"\nFAIL: core delegate accuracy {delegate_acc:.1%} below threshold {min_accuracy:.1%}")
        exit_code = 1
    if followup_acc < min_accuracy:
        print(
            f"\nFAIL: follow-up delegate accuracy {followup_acc:.1%} "
            f"below threshold {min_accuracy:.1%}"
        )
        exit_code = 1
    # The challenge floor is a soft regression guard: it only trips if the
    # router gets *dramatically* worse on hard cases. Default 0.0 = report-only.
    if challenge_delegate_acc < challenge_floor:
        print(
            f"\nFAIL: challenge delegate accuracy {challenge_delegate_acc:.1%} "
            f"below floor {challenge_floor:.1%}"
        )
        exit_code = 1
    if exit_code == 0:
        print("\nOK: router meets thresholds.")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-accuracy", type=float, default=0.80,
                        help="Gate on CORE delegate accuracy.")
    parser.add_argument("--challenge-floor", type=float, default=0.0,
                        help="Optional gate on CHALLENGE delegate accuracy (0 = report-only).")
    args = parser.parse_args()
    return evaluate(args.min_accuracy, args.challenge_floor)


if __name__ == "__main__":
    sys.exit(main())
