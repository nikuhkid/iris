"""
iris/decision_engine.py

Final authority. No model touches this layer.
Takes plan_analysis_final output and returns a verdict.

Verdicts: proceed | require_confirmation | reject
Phase 1 — explicit rules only. Scoring layer deferred to Phase 2.
"""

# Actions considered irreversible — always confirm regardless of risk rating
IRREVERSIBLE_ACTIONS = {"delete_file", "remove_entry", "purge", "file_overwrite", "file_write"}

# Actions that affect system config — always confirm
SYSTEM_CONFIG_ACTIONS = {"update_config", "write_config", "modify_fstab", "systemctl", "edit_configuration"}


def decide(analysis: dict, plan: dict) -> dict:
    """
    Apply hard rules to plan_analysis_final output.

    Returns:
        {
            "verdict": "proceed" | "require_confirmation" | "reject",
            "reason": str
        }
    """
    op   = analysis["operation_flags"]
    sf   = analysis["state_flags"]
    sig  = analysis["intent_signals"]
    step_actions = {s.get("action", "") for s in plan.get("steps", [])}

    # Rule 1 — unknown action type → always reject
    if op["unknown"]["value"]:
        unknown_actions = [
            s.get("action") for s in plan.get("steps", [])
            if s.get("action") not in _known_actions()
        ]
        return {
            "verdict": "reject",
            "reason": f"unknown action type(s): {unknown_actions} — cannot assess safety"
        }

    # Rule 2 — irreversible action → always confirm, never scored away
    irreversible = step_actions & IRREVERSIBLE_ACTIONS
    if irreversible:
        return {
            "verdict": "require_confirmation",
            "reason": f"irreversible action(s) detected: {sorted(irreversible)}"
        }

    # Rule 3 — multi-step plan with state change → always confirm
    if sf["multi_step"]["value"] and sf["state_change"]["value"]:
        return {
            "verdict": "require_confirmation",
            "reason": "multi-step plan with state change — confirm before execution"
        }

    # Rule 4 — system config action → always confirm
    sys_config = step_actions & SYSTEM_CONFIG_ACTIONS
    if sys_config:
        return {
            "verdict": "require_confirmation",
            "reason": f"system config action(s) detected: {sorted(sys_config)}"
        }

    # Rule 5 — implicit destructive → require confirmation
    if sig["implicit_destructive"]["value"]:
        return {
            "verdict": "require_confirmation",
            "reason": "implicit destructive intent detected"
        }

    # Rule 6 — explicit destructive intent declared → require confirmation
    if sig["explicit_destructive"]["value"]:
        return {
            "verdict": "require_confirmation",
            "reason": "plan declared as destructive intent"
        }

    return {
        "verdict": "proceed",
        "reason": "no blocking rules triggered"
    }


def _known_actions() -> set:
    """Load known actions from action_map to support unknown detection."""
    import json
    from pathlib import Path
    action_map_path = Path(__file__).parent.parent / "config" / "action_map.json"
    with open(action_map_path) as f:
        data = json.load(f)
    known = set()
    for k, v in data.items():
        if not k.startswith("$"):
            known.update(v)
    return known
