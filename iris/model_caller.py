"""
iris/model_caller.py

Shared planning model caller with retry logic.
Used by pipeline (slot_1) and slot_2 — model name is the only variable.

Retry policy:
    - invalid_json: retry — technical failure, different sample may succeed
    - schema_violation: retry — model produced JSON but wrong shape, worth one more try
    - cannot_plan: no retry — ambiguous input, needs user clarification

Return dict always includes:
    - all standard validation fields (valid, error, plan, etc.)
    - attempts: int — number of attempts made (1-based)
    - raw_output: str — raw model output from the final attempt
"""

import json
import urllib.request
from pathlib import Path

from iris.validator import validate

PROMPT_PATH      = Path(__file__).parent.parent / "prompts" / "planning_system.txt"
OLLAMA_URL       = "http://localhost:11434/api/chat"
MAX_RETRIES      = 2
RETRYABLE_ERRORS = {"invalid_json", "schema_violation"}


def _call_model(model_name: str, user_input: str) -> str:
    """Call a planning model slot. Returns raw model output string."""
    system_prompt = PROMPT_PATH.read_text()
    payload = {
        "model": model_name,
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


def call_with_retry(model_name: str, user_input: str) -> dict:
    """
    Call planning model and validate. Retry up to MAX_RETRIES on retryable failures.
    Returns final validation result dict, enriched with:
        - attempts: int — how many attempts were made
        - raw_output: str — raw model output from the final attempt

    No logging here — caller owns the run_id and logs around this.
    """
    attempts = 0
    raw = None
    validation = None

    while attempts <= MAX_RETRIES:
        attempt_label = f"attempt {attempts + 1}/{MAX_RETRIES + 1}"
        print(f"[model_caller] calling {model_name} ({attempt_label})...")

        raw = _call_model(model_name, user_input)
        print(f"[model_caller] raw output: {raw}")

        validation = validate(raw)

        if validation["valid"]:
            if attempts > 0:
                print(f"[model_caller] retry succeeded on {attempt_label}")
            validation["attempts"]   = attempts + 1
            validation["raw_output"] = raw
            return validation

        error = validation.get("error")

        if error not in RETRYABLE_ERRORS:
            print(f"[model_caller] {error} — no retry")
            validation["attempts"]   = attempts + 1
            validation["raw_output"] = raw
            return validation

        print(f"[model_caller] {error} — retrying ({attempt_label})")
        attempts += 1

    print(f"[model_caller] all {MAX_RETRIES + 1} attempts failed — last error: {validation.get('error')}")
    validation["attempts"]   = MAX_RETRIES + 1
    validation["raw_output"] = raw
    return validation
