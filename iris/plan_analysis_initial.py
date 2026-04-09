"""
iris/plan_analysis_initial.py

Pass 1 — extract deterministic facts from a validated plan.
No interpretation, no heuristics, no judgment.
Unknown actions are flagged, not guessed.
"""

import json
from pathlib import Path

ACTION_MAP_PATH = Path(__file__).parent.parent / "config" / "action_map.json"


def _load_action_map() -> dict:
    with open(ACTION_MAP_PATH) as f:
        data = json.load(f)
    # strip $comment if present
    return {k: v for k, v in data.items() if not k.startswith("$")}


def _classify_action(action: str, action_map: dict) -> str:
    for op_type, actions in action_map.items():
        if action in actions:
            return op_type
    return "unknown"


def _extract_raw_terms(plan: dict) -> list[str]:
    """
    Lexical scan of action strings, arg keys, and file path values.
    Staging only — not interpreted here. Consumed by plan_analysis_final.
    """
    terms = []
    for step in plan.get("steps", []):
        terms.append(step.get("action", ""))
        args = step.get("args", {})
        for k, v in args.items():
            terms.append(k)
            if isinstance(v, str):
                terms.append(v)
    return [t.lower() for t in terms if t]


def analyze(plan: dict) -> dict:
    """
    Extract deterministic facts from a validated plan.

    Returns:
        {
            "operation_flags": {
                "read":    {"value": bool, "source": "action_type"},
                "write":   {"value": bool, "source": "action_type"},
                "delete":  {"value": bool, "source": "action_type"},
                "unknown": {"value": bool, "source": "action_type"}
            },
            "state_flags": {
                "multi_step":        {"value": bool},
                "state_change":      {"value": bool},
                "idempotent":        {"value": bool},
                "bounded_operation": {"value": bool, "source": "args_constraints"}
            },
            "raw_terms_detected": {
                "value": [str],
                "source": "lexical_scan",
                "note": "staging only — not interpreted at this pass"
            }
        }
    """
    action_map = _load_action_map()
    steps = plan.get("steps", [])

    classifications = [_classify_action(s.get("action", ""), action_map) for s in steps]

    has_read    = "read"    in classifications
    has_write   = "write"   in classifications
    has_delete  = "delete"  in classifications
    has_unknown = "unknown" in classifications

    multi_step   = len(steps) > 1
    state_change = has_write or has_delete
    # idempotent: read-only plans with no write/delete/unknown are idempotent
    idempotent   = has_read and not has_write and not has_delete and not has_unknown
    # bounded: all steps have at least one arg constraining scope
    bounded      = all(bool(s.get("args")) for s in steps)

    return {
        "operation_flags": {
            "read":    {"value": has_read,    "source": "action_type"},
            "write":   {"value": has_write,   "source": "action_type"},
            "delete":  {"value": has_delete,  "source": "action_type"},
            "unknown": {"value": has_unknown, "source": "action_type"}
        },
        "state_flags": {
            "multi_step":        {"value": multi_step},
            "state_change":      {"value": state_change},
            "idempotent":        {"value": idempotent},
            "bounded_operation": {"value": bounded, "source": "args_constraints"}
        },
        "raw_terms_detected": {
            "value": _extract_raw_terms(plan),
            "source": "lexical_scan",
            "note": "staging only — not interpreted at this pass"
        }
    }
