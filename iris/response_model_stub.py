"""
iris/response_model_stub.py

STUB — Phase 1 only.
Formats verdict into a human-readable string. No model call.
Replace with real response_model when DPO-tuned model is ready.

Interface contract (must be preserved in real implementation):
    Input:  verdict dict from decision_engine.decide()
            plan dict (validated)
    Output: {"response": str}

Constraints the real response_model must honour:
    - Cannot claim it executed anything
    - Cannot claim it enforced anything
    - Cannot claim it made any decisions
    - Proposes and expresses only — system is the authority
"""


def respond(verdict: dict, plan: dict) -> dict:
    """
    Format decision_engine verdict into a response string.

    Returns:
        {"response": str}
    """
    v = verdict["verdict"]
    reason = verdict["reason"]
    intent = plan.get("intent", "unknown")
    actions = [s.get("action", "") for s in plan.get("steps", [])]
    step_count = len(actions)

    if v == "proceed":
        response = (
            f"Plan ready to execute. "
            f"Intent: {intent}. "
            f"{step_count} step(s): {', '.join(actions)}."
        )
    elif v == "require_confirmation":
        response = (
            f"Confirmation required before proceeding. "
            f"Reason: {reason}. "
            f"Intent: {intent}. "
            f"{step_count} step(s): {', '.join(actions)}."
        )
    elif v == "reject":
        response = (
            f"Plan rejected. "
            f"Reason: {reason}."
        )
    else:
        response = f"Unknown verdict: {v}."

    return {"response": response}
