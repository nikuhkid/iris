"""
iris/dry_run.py

Produces a structured, unambiguous summary of what a plan intends to do.
Generated after decision_engine verdict, before user approval.

Not display-ready — that is the approval loop's job.
No internal field names, no raw dicts — plain values only.
"""

import json
from pathlib import Path

ACTION_MAP_PATH = Path(__file__).parent.parent / "config" / "action_map.json"


def _load_action_map() -> dict:
    with open(ACTION_MAP_PATH) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("$")}


def _classify_action(action: str, action_map: dict) -> str:
    for op_type, actions in action_map.items():
        if action in actions:
            return op_type
    return "unknown"


def _extract_target(args: dict) -> str:
    """Return first string value from args, or 'no target' if none."""
    for v in args.values():
        if isinstance(v, str):
            return v
    return "no target"


def summarize(verdict: dict, plan: dict, analysis: dict) -> dict:
    """
    Produce a structured dry-run summary.

    Args:
        verdict:  output of decision_engine.decide()
        plan:     validated plan dict
        analysis: output of plan_analysis_final.analyze()

    Returns:
        {
            "intent":         str,
            "step_count":     int,
            "steps": [
                {
                    "action": str,
                    "target": str,
                    "risk":   str
                },
                ...
            ],
            "operations": {
                "reads":    int,
                "writes":   int,
                "deletes":  int,
                "unknowns": int
            },
            "verdict":        str,
            "verdict_reason": str
        }
    """
    action_map = _load_action_map()
    steps = plan.get("steps", [])

    step_summaries = []
    reads = writes = deletes = unknowns = 0

    for step in steps:
        action = step.get("action", "")
        args   = step.get("args", {})
        risk   = step.get("risk", "unknown")
        target = _extract_target(args)
        op_type = _classify_action(action, action_map)

        if op_type == "read":
            reads += 1
        elif op_type == "write":
            writes += 1
        elif op_type == "delete":
            deletes += 1
        else:
            unknowns += 1

        step_summaries.append({
            "action": action,
            "target": target,
            "risk":   risk
        })

    return {
        "intent":     plan.get("intent", "unknown"),
        "step_count": len(steps),
        "steps":      step_summaries,
        "operations": {
            "reads":    reads,
            "writes":   writes,
            "deletes":  deletes,
            "unknowns": unknowns
        },
        "verdict":        verdict["verdict"],
        "verdict_reason": verdict["reason"]
    }
