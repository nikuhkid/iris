"""
iris/slot2.py

Slot 2 — independent validator for state-changing plans.

Receives original_input only — never slot_1 output.
Independence is the point: same base model, different sample, different temperature.
Catches surface drift, not deep model bias. Architecture doesn't claim more than that.

Triggered only when state_change is true (write OR delete).
"""

from iris.model_caller import call_with_retry

SLOT2_MODEL = "iris-slot2"


def call(original_input: str) -> dict:
    """
    Call slot_2 with the original user input.
    Returns validation result dict from call_with_retry —
    same shape as slot_1, including attempts and raw_output.
    """
    return call_with_retry(SLOT2_MODEL, original_input)
