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


def evaluate(min_accuracy: float) -> int:
    router = RouterAgent()
    testset_path = Path(__file__).parent / "intent_testset.json"
    cases = load_testset(testset_path)

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

    total = len(cases)
    intent_acc = intent_hits / total
    delegate_acc = delegate_hits / total

    print(f"Intent router eval — {total} cases")
    print(f"  intent accuracy   : {intent_hits}/{total} = {intent_acc:.1%}")
    print(f"  delegate accuracy : {delegate_hits}/{total} = {delegate_acc:.1%}")

    if failures:
        print("\nFailures:")
        for case, got_intent, got_delegate in failures:
            print(
                f"  - '{case['message'][:50]}' "
                f"expected {case['expected_intent']}/{case['expected_delegate']}, "
                f"got {got_intent}/{got_delegate}"
            )

    if delegate_acc < min_accuracy:
        print(
            f"\nFAIL: delegate accuracy {delegate_acc:.1%} "
            f"below threshold {min_accuracy:.1%}"
        )
        return 1
    print("\nOK: router meets accuracy threshold.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    args = parser.parse_args()
    return evaluate(args.min_accuracy)


if __name__ == "__main__":
    sys.exit(main())
