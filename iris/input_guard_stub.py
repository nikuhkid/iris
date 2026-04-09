"""
iris/input_guard_stub.py

STUB — Phase 1 only.
Pass-through input guard. Logs what it received, returns it clean.
No validation, no injection detection, no encoding checks.

Replace with real input_guard in Phase 2 when real traffic data exists.

Interface contract (must be preserved in real implementation):
    Input:  raw user input string
    Output: {"passed": bool, "content": str, "reason": str|None}

Real input_guard must implement:
    - max_length check
    - encoding validation
    - prompt injection pattern detection (AgentShield/MiniClaw catalogue)
    - command injection detection
    - rejection reason codes: encoding_invalid, max_length_exceeded,
      injection_pattern_detected, command_injection_detected, unclassified_rejection
"""


def guard(raw: str) -> dict:
    """
    Pass-through. Logs input, returns clean.

    Returns:
        {"passed": True, "content": str, "reason": None}
    """
    print(f"[input_guard_stub] received input ({len(raw)} chars)")
    return {
        "passed": True,
        "content": raw,
        "reason": None
    }
