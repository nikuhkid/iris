"""
iris/pipeline.py

Phase 4 pipeline entry point.
Wires all components in order — no logic lives here, only sequencing.

Flow:
    input
    → input_guard_stub
    → translation_layer (slot1) with retry (max 2, on invalid_json/schema_violation only)
    → validator
    → plan_analysis_initial
    → [slot_2 + comparator if state_change: true]
    → plan_analysis_final
    → decision_engine
    → dry_run summary
    → user approval (approve | modify | reject | kill)
    → response_model_stub

Selective redundancy:
    slot_2 is triggered only when state_change is true (write OR delete).
    slot_2 receives original_input — never slot_1 output.
    Mismatch → structured conflict returned to user. No auto-resolution.

Retry policy delegated to model_caller.call_with_retry.

User approval:
    approve → pipeline continues to response
    reject | kill → pipeline stops, user_action logged, result returned to caller
    modify → amended_input returned to caller — caller owns re-invocation and depth tracking

Logging:
    - Every run is logged to iris.db via iris.logger
    - start_run() at entry, update_run() at each stage
    - Logging never raises — failures are printed and swallowed
"""

from iris.input_guard_stub import guard
from iris.plan_analysis_initial import analyze as pass1
from iris.plan_analysis_final import analyze as pass2
from iris.decision_engine import decide
from iris.dry_run import summarize as dry_run_summarize
from iris.approval_loop import prompt as approval_prompt
from iris.response_model_stub import respond
from iris.model_caller import call_with_retry
from iris.slot2 import call as slot2_call
from iris.comparator import compare
from iris import logger

SLOT1_MODEL = "iris-slot1"


def _log(run_id: str, **fields) -> None:
    """Fire-and-forget logger update. Never raises."""
    try:
        logger.update_run(run_id, **fields)
    except Exception as e:
        print(f"[pipeline] logger update failed: {e}")


def run(user_input: str, source: str = "cli", user_id: str = None) -> dict:
    """
    Run the full Phase 4 pipeline.

    Args:
        user_input: raw user input string
        source:     'cli' | 'discord' | 'claude_interface'
        user_id:    external user identifier — None for CLI runs

    Returns:
        {
            "run_id":       str,
            "response":     str,
            "verdict":      str,
            "plan":         dict | None,
            "analysis":     dict | None,
            "dry_run":      dict | None,
            "user_action":  str,           -- approve | reject | kill | modify
            "amended_input": str | None,   -- only present when user_action == modify
            "error":        str | None
        }
    """
    print(f"\n{'='*60}")
    print(f"INPUT: {user_input}")
    print(f"{'='*60}")

    # Initialise DB and open log row
    logger.init_db()
    run_id = logger.start_run(source=source, user_id=user_id)
    _log(run_id, raw_input=user_input)

    # Stage 1 — input guard
    guarded = guard(user_input)
    _log(
        run_id,
        guarded_input=guarded.get("content"),
        guard_passed=1 if guarded["passed"] else 0,
    )

    if not guarded["passed"]:
        _log(run_id, pipeline_error=guarded["reason"], verdict="reject")
        response = f"Input rejected: {guarded['reason']}"
        _log(run_id, response=response)
        return {
            "run_id": run_id,
            "response": response,
            "verdict": "reject",
            "plan": None,
            "analysis": None,
            "error": guarded["reason"]
        }

    # Stage 2 — translation layer with retry
    validation = call_with_retry(SLOT1_MODEL, guarded["content"])
    _log(
        run_id,
        slot_used=1,
        attempts=validation.get("attempts"),
        raw_model_output=validation.get("raw_output"),
        valid_json=1 if validation.get("valid") or validation.get("error") != "invalid_json" else 0,
        valid_schema=1 if validation.get("valid") else 0,
    )

    if not validation["valid"]:
        error = validation.get("error")
        detail = validation.get("detail") or validation.get("reason")
        if error == "cannot_plan":
            response = f"Cannot plan — input is ambiguous. {detail}. Please clarify."
        else:
            response = f"Plan invalid after {validation.get('attempts')} attempt(s): {error} — {detail}"
        _log(run_id, pipeline_error=error, verdict="reject", response=response)
        return {
            "run_id": run_id,
            "response": response,
            "verdict": "reject",
            "plan": None,
            "analysis": None,
            "error": error
        }

    plan = validation["plan"]
    print(f"[pipeline] plan valid: {plan['intent']}, {len(plan['steps'])} step(s)")
    _log(run_id, plan_json=plan)

    # Stage 3 — plan_analysis_initial
    analysis_initial = pass1(plan)
    _log(run_id, analysis_initial=analysis_initial)

    # Stage 4 — selective redundancy (slot_2 + comparator)
    # Triggered only when state_change is true (write OR delete).
    # slot_2 receives original_input — never slot_1 output.
    if analysis_initial["state_flags"]["state_change"]["value"]:
        print(f"[pipeline] state_change detected — triggering slot_2...")
        slot2_validation = slot2_call(guarded["content"])
        _log(run_id, slot_used=2)

        if not slot2_validation["valid"]:
            # slot_2 failed to produce a valid plan — escalate, don't proceed
            error = slot2_validation.get("error")
            response = (
                f"Slot 2 validation failed ({error}) on a state-changing plan. "
                f"Cannot confirm intent. Please clarify or retry."
            )
            _log(run_id, pipeline_error=f"slot2_{error}", verdict="reject", response=response)
            return {
                "run_id":   run_id,
                "response": response,
                "verdict":  "reject",
                "plan":     None,
                "analysis": None,
                "error":    f"slot2_{error}"
            }

        comparison = compare(plan, slot2_validation["plan"])
        print(f"[pipeline] comparator: match={comparison['match']}")

        if not comparison["match"]:
            conflict = comparison["conflict"]
            parts = []
            if conflict["intent_mismatch"]:
                parts.append(
                    f"intent: slot_1={comparison['slot1']['intent']!r} "
                    f"vs slot_2={comparison['slot2']['intent']!r}"
                )
            if conflict["actions_mismatch"]:
                parts.append(
                    f"actions: slot_1={comparison['slot1']['actions']} "
                    f"vs slot_2={comparison['slot2']['actions']}"
                )
            response = (
                f"Slot conflict detected — plans disagree on {', '.join(parts)}. "
                f"Cannot proceed without clarification."
            )
            _log(run_id, pipeline_error="slot_conflict", verdict="reject", response=response)
            return {
                "run_id":     run_id,
                "response":   response,
                "verdict":    "reject",
                "plan":       None,
                "analysis":   None,
                "error":      "slot_conflict",
                "comparison": comparison
            }

        print(f"[pipeline] slot_2 agrees — proceeding")

    # Stage 5 — plan_analysis_final
    analysis_final = pass2(analysis_initial, plan)
    _log(run_id, analysis_final=analysis_final)

    # Stage 6 — decision engine
    verdict = decide(analysis_final, plan)
    print(f"[pipeline] verdict: {verdict['verdict']} — {verdict['reason']}")
    _log(run_id, verdict=verdict["verdict"], verdict_reason=verdict["reason"])

    # Stage 7 — dry-run summary
    dry_run = dry_run_summarize(verdict, plan, analysis_final)

    # Stage 8 — user approval
    approval = approval_prompt(dry_run)
    _log(run_id, user_action=approval["action"])

    if approval["action"] != "approve":
        response = f"Stopped at user request: {approval['action']}."
        _log(run_id, response=response)
        result = {
            "run_id":        run_id,
            "response":      response,
            "verdict":       verdict["verdict"],
            "plan":          plan,
            "analysis":      analysis_final,
            "dry_run":       dry_run,
            "user_action":   approval["action"],
            "error":         None
        }
        if approval["action"] == "modify":
            result["amended_input"] = approval["amended_input"]
        return result

    # Stage 9 — response
    response = respond(verdict, plan)
    _log(run_id, response=response["response"])

    return {
        "run_id":      run_id,
        "response":    response["response"],
        "verdict":     verdict["verdict"],
        "plan":        plan,
        "analysis":    analysis_final,
        "dry_run":     dry_run,
        "user_action": "approve",
        "error":       None
    }


if __name__ == "__main__":
    import sys
    user_input = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Read the file at /tmp/test.txt"
    result = run(user_input)
    print(f"\n{'='*60}")
    print(f"RESPONSE: {result['response']}")
    print(f"run_id:   {result['run_id']}")
    print(f"{'='*60}\n")
