"""
iris/validator.py

Schema validator for IRIS plan output.
Accepts raw model output string.
Returns structured result — never raises.
Hard fail on invalid JSON or schema violation.
"""

import json
import jsonschema
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "plan.json"


def _load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate(raw: str) -> dict:
    """
    Validate raw model output string against the plan schema.

    Returns:
        On success:
            {"valid": True, "plan": <parsed plan dict>}
        On JSON parse failure:
            {"valid": False, "error": "invalid_json", "detail": <str>}
        On schema violation:
            {"valid": False, "error": "schema_violation", "detail": <str>}
        On structured model error (cannot_plan):
            {"valid": False, "error": "cannot_plan", "reason": <str>}
    """
    # Step 1 — parse JSON
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "error": "invalid_json",
            "detail": str(e)
        }

    # Step 2 — check for structured model failure
    if isinstance(parsed, dict) and parsed.get("error") == "cannot_plan":
        return {
            "valid": False,
            "error": "cannot_plan",
            "reason": parsed.get("reason", "no reason provided")
        }

    # Step 3 — validate against schema
    schema = _load_schema()
    try:
        jsonschema.validate(instance=parsed, schema=schema)
    except jsonschema.ValidationError as e:
        return {
            "valid": False,
            "error": "schema_violation",
            "detail": e.message
        }

    return {
        "valid": True,
        "plan": parsed
    }
