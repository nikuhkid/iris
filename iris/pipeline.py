"""
iris/pipeline.py

Phase 2 pipeline entry point.
Wires all components in order — no logic lives here, only sequencing.

Flow:
    input
    → input_guard_stub
    → translation_layer (slot1) with retry (max 2, on invalid_json/schema_violation only)
    → validator
    → plan_analysis_initial
    → plan_analysis_final
    → decision_engine
    → response_model_stub

Retry policy:
    - invalid_json: retry — technical failure, different sample may succeed
    - schema_violation: retry — model produced JSON but wrong shape, worth one more try
    - cannot_plan: no retry — ambiguous input, model made a judgment call, needs clarification from user
"""

import json
import urllib.request
from pathlib import Path

from iris.input_guard_stub import guard
from iris.validator import validate
from iris.plan_analysis_initial import analyze as pass1
from iris.plan_analysis_final import analyze as pass2
from iris.decision_engine import decide
from iris.response_model_stub import respond

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planning_system.txt"
OLLAMA_URL  = "http://localhost:11434/api/chat"
SLOT1_MODEL = "iris-slot1"
MAX_RETRIES = 2

RETRYABLE_ERRORS = {"invalid_json", "schema_violation"}


def _call_planning_model(user_input: str) -> str:
    """Call slot1 with the planning system prompt. Returns raw model output string."""
    system_prompt = PROMPT_PATH.read_text()
    payload = {
        "model": SLOT1_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input}
        ]
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    return data["message"]["content"]


def _call_with_retry(user_input: str) -> dict:
    """
    Call planning model and validate. Retry up to MAX_RETRIES on retryable failures.
    Returns final validation result.

    Retry on: invalid_json, schema_violation
    No retry on: cannot_plan (ambiguous input — needs user clarification, not another attempt)
    """
    attempts = 0
    last_validation = None

    while attempts <= MAX_RETRIES:
        attempt_label = f"attempt {attempts + 1}/{MAX_RETRIES + 1}"
        print(f"[pipeline] calling planning model ({attempt_label})...")

        raw = _call_planning_model(user_input)
        print(f"[pipeline] raw model output: {raw}")

        validation = validate(raw)

        if validation["valid"]:
            if attempts > 0:
                print(f"[pipeline] retry succeeded on {attempt_label}")
            return validation

        error = validation.get("error")

        if error not in RETRYABLE_ERRORS:
            print(f"[pipeline] {error} — no retry")
            return validation

        print(f"[pipeline] {error} — retrying ({attempt_label})")
        last_validation = validation
        attempts += 1

    print(f"[pipeline] all {MAX_RETRIES + 1} attempts failed — last error: {last_validation.get('error')}")
    return last_validation


def run(user_input: str) -> dict:
    """
    Run the full Phase 2 pipeline.

    Returns:
        {
            "response": str,
            "verdict": str,
            "plan": dict | None,
            "analysis": dict | None,
            "error": str | None
        }
    """
    print(f"\n{'='*60}")
    print(f"INPUT: {user_input}")
    print(f"{'='*60}")

    # Stage 1 — input guard
    guarded = guard(user_input)
    if not guarded["passed"]:
        return {
            "response": f"Input rejected: {guarded['reason']}",
            "verdict": "reject",
            "plan": None,
            "analysis": None,
            "error": guarded["reason"]
        }

    # Stage 2 — translation layer with retry
    validation = _call_with_retry(guarded["content"])

    if not validation["valid"]:
        error = validation.get("error")
        detail = validation.get("detail") or validation.get("reason")
        if error == "cannot_plan":
            response = f"Cannot plan — input is ambiguous. {detail}. Please clarify."
        else:
            response = f"Plan invalid after {MAX_RETRIES + 1} attempts: {error} — {detail}"
        return {
            "response": response,
            "verdict": "reject",
            "plan": None,
            "analysis": None,
            "error": error
        }

    plan = validation["plan"]
    print(f"[pipeline] plan valid: {plan['intent']}, {len(plan['steps'])} step(s)")

    # Stage 3 — plan_analysis_initial
    analysis_initial = pass1(plan)

    # Stage 4 — plan_analysis_final
    analysis_final = pass2(analysis_initial, plan)

    # Stage 5 — decision engine
    verdict = decide(analysis_final, plan)
    print(f"[pipeline] verdict: {verdict['verdict']} — {verdict['reason']}")

    # Stage 6 — response
    response = respond(verdict, plan)

    return {
        "response": response["response"],
        "verdict": verdict["verdict"],
        "plan": plan,
        "analysis": analysis_final,
        "error": None
    }


if __name__ == "__main__":
    import sys
    user_input = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Read the file at /tmp/test.txt"
    result = run(user_input)
    print(f"\n{'='*60}")
    print(f"RESPONSE: {result['response']}")
    print(f"{'='*60}\n")
