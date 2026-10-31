"""Opt-in synthetic question probe, never started by an API import or test."""

import argparse
import json
import time

from .gemma import PROMPT_VERSION, GemmaQuestions
from .questions import QuestionError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--max-tokens", required=True, type=int)
    parser.add_argument("--max-response-bytes", required=True, type=int)
    args = parser.parse_args()
    started = time.monotonic()
    result = {"prompt_version": PROMPT_VERSION, "probe": "synthetic_question"}
    try:
        client = GemmaQuestions(**vars(args))
        draft = client.generate({
            "profile_revision": 1,
            "confirmed_answers": {"field_code": "ai_data", "experience": "basic"},
            "confirmed_evidence": [],
            "eligible_slots": ["learning_goal"],
            "allowed_companion_slots": {"learning_goal": ["goal_text"]},
            "resolved_unknown_slots": [],
        })
        result.update(status="valid", target_slot=draft.target_slot, options=len(draft.options))
    except (QuestionError, ValueError) as error:
        result.update(status="failed", error=str(error))
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
