"""
iris/comparator.py

Compares slot_1 and slot_2 plans for intent and action type agreement.

Normalization: sorted sets only — order doesn't matter, strings do.
`delete_file` and `remove_file` are different strings and will mismatch.
That's intentional — semantic equivalence is a human call, not a comparator call.

Returns:
    {
        "match":   bool,
        "slot1":   {"intent": str, "actions": [str]},
        "slot2":   {"intent": str, "actions": [str]},
        "conflict": {
            "intent_mismatch":  bool,
            "actions_mismatch": bool
        } | None  — None when match is True
    }
"""


def _extract(plan: dict) -> tuple[str, list[str]]:
    """Extract intent and sorted action list from a validated plan."""
    intent  = plan.get("intent", "")
    actions = sorted(step.get("action", "") for step in plan.get("steps", []))
    return intent, actions


def compare(plan1: dict, plan2: dict) -> dict:
    """
    Compare two validated plans from slot_1 and slot_2.
    Exact match on intent string and sorted action list.
    """
    intent1, actions1 = _extract(plan1)
    intent2, actions2 = _extract(plan2)

    intent_match  = intent1 == intent2
    actions_match = actions1 == actions2
    match         = intent_match and actions_match

    return {
        "match": match,
        "slot1": {"intent": intent1, "actions": actions1},
        "slot2": {"intent": intent2, "actions": actions2},
        "conflict": None if match else {
            "intent_mismatch":  not intent_match,
            "actions_mismatch": not actions_match
        }
    }
