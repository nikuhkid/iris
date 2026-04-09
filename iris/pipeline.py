"""
iris/pipeline.py

Phase 1 pipeline entry point.
Wires all components in order — no logic lives here, only sequencing.

Flow:
    input
    → input_guard_stub
    → translation_layer (Ollama slot1)
    → validator
    → plan_analysis_initial
    → plan_analysis_final
    → decision_engine
    → response_model_stub
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


def run(user_input: str) -> dict:
    """
    Run the full Phase 1 pipeline.

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

    # Stage 2 — translation layer (planning model)
    print("[pipeline] calling planning model...")
    raw = _call_planning_model(guarded["content"])
    print(f"[pipeline] raw model output: {raw}")

    # Stage 3 — validate
    validation = validate(raw)
    if not validation["valid"]:
        return {
            "response": f"Plan invalid: {validation['error']} — {validation.get('detail') or validation.get('reason')}",
            "verdict": "reject",
            "plan": None,
            "analysis": None,
            "error": validation["error"]
        }

    plan = validation["plan"]
    print(f"[pipeline] plan valid: {plan['intent']}, {len(plan['steps'])} step(s)")

    # Stage 4 — plan_analysis_initial
    analysis_initial = pass1(plan)

    # Stage 5 — plan_analysis_final
    analysis_final = pass2(analysis_initial, plan)

    # Stage 6 — decision engine
    verdict = decide(analysis_final, plan)
    print(f"[pipeline] verdict: {verdict['verdict']} — {verdict['reason']}")

    # Stage 7 — response
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
