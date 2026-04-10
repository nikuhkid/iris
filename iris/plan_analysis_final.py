"""
iris/plan_analysis_final.py

Pass 2 — interpret facts from pass 1. Adds intent signals.
Facts from pass 1 are immutable — this pass cannot modify them.
"""

IMPLICIT_DESTRUCTIVE_TERMS = {
    "chuck", "zero", "tidy", "trim",
    "clean", "clear", "remove", "prune", "reset", "purge", "wipe", "flush"
}


def _step1_lexical_match(raw_terms: list[str]) -> set[str]:
    """Returns matched destructive terms found in raw_terms_detected."""
    return IMPLICIT_DESTRUCTIVE_TERMS & set(raw_terms)


def _step2_scope_gate(operation_flags: dict) -> bool:
    """Write or delete must be true — term match alone is not enough."""
    return (
        operation_flags["write"]["value"] or
        operation_flags["delete"]["value"]
    )


def _step3_argument_context(matched_terms: set[str], plan: dict) -> bool:
    """
    Matched term must appear in action strings or arg keys/values.
    Not in unrelated text fields or commentary.
    """
    for step in plan.get("steps", []):
        action = step.get("action", "").lower()
        if any(t in action for t in matched_terms):
            return True
        for k, v in step.get("args", {}).items():
            if any(t in k.lower() for t in matched_terms):
                return True
            if isinstance(v, str) and any(t in v.lower() for t in matched_terms):
                return True
    return False


def analyze(pass1: dict, plan: dict) -> dict:
    """
    Interpret facts from pass 1. Append intent signals only.
    pass1: output of plan_analysis_initial.analyze()
    plan:  original validated plan (needed for argument context check)

    Returns pass1 output extended with intent_signals.
    """
    raw_terms = pass1["raw_terms_detected"]["value"]
    operation_flags = pass1["operation_flags"]

    # Three-step gate
    matched_terms  = _step1_lexical_match(raw_terms)
    scope_ok       = _step2_scope_gate(operation_flags) if matched_terms else False
    arg_context_ok = _step3_argument_context(matched_terms, plan) if scope_ok else False

    implicit_destructive = bool(matched_terms and scope_ok and arg_context_ok)

    # explicit_destructive: intent field directly declared destructive
    explicit_destructive = plan.get("intent") == "destructive"

    result = dict(pass1)  # pass1 facts are immutable — copy, never modify
    result["intent_signals"] = {
        "implicit_destructive": {
            "value": implicit_destructive,
            "source": "three_step_gate" if implicit_destructive else "gate_not_passed"
        },
        "explicit_destructive": {
            "value": explicit_destructive,
            "source": "action_type"
        }
    }
    return result
