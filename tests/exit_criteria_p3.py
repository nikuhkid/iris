"""
tests/exit_criteria_p3.py

Phase 3 exit criteria test.

Exit criteria (pass/fail gates):
    1. slot_2 called and returned valid on all 20 state-change prompts
    2. slot_2 never called on any of the 20 read-only prompts
    3. All 5 mocked conflicts surfaced as structured slot_conflict — no crashes, no silent failures

Agreement rate (data only — not a gate):
    Recorded per state-change prompt. Feeds Phase 4 calibration.

Results saved to tests/exit_criteria_p3_results.json.

Usage:
    cd /home/nikuhkid/iris && python3 tests/exit_criteria_p3.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from iris.pipeline import run as pipeline_run

RESULTS_PATH = ROOT / "tests" / "exit_criteria_p3_results.json"

# ── prompts ───────────────────────────────────────────────────────────────────

STATE_CHANGE_PROMPTS = [
    "Write 'hello world' to /tmp/iris_test.txt using write_file",
    "Save the string 'test data' to /tmp/output.txt using write_file",
    "Append 'new entry' to /tmp/log.txt using append_file",
    "Store the result 'ok' in /tmp/result.txt using write_file",
    "Write the config value 'debug=true' to /tmp/config.txt using write_file",
    "Save 'session started' to /tmp/session.log using write_file",
    "Write 'line 1' to /tmp/a.txt and 'line 2' to /tmp/b.txt using write_file",
    "Update /tmp/notes.txt with the content 'updated' using write_file",
    "Write the output 'done' to /tmp/status.txt using write_file",
    "Save 'error: none' to /tmp/errors.log using write_file",
    "Delete the file at /tmp/test.txt using delete_file",
    "Remove /tmp/old_log.txt using remove_file",
    "Delete /tmp/cache.txt using delete_file",
    "Remove the file /tmp/temp.txt using remove_file",
    "Delete /tmp/output.txt using delete_file",
    "Remove /tmp/iris_test.txt using remove_file",
    "Delete the file /tmp/session.log using delete_file",
    "Remove /tmp/result.txt using remove_file",
    "Delete /tmp/status.txt using delete_file",
    "Remove the file at /tmp/errors.log using remove_file",
]

READ_ONLY_PROMPTS = [
    "Read the file at /tmp/test.txt and summarize its contents",
    "List the files in /tmp",
    "Read /tmp/config.txt",
    "Summarize the contents of /tmp/notes.txt",
    "Search for the word 'error' in /tmp/log.txt",
    "Read the file /tmp/output.txt",
    "List all files in /tmp/iris",
    "Get the contents of /tmp/status.txt",
    "Read /tmp/session.log and show the last entry",
    "Summarize /tmp/errors.log",
    "Read the file at /tmp/a.txt",
    "List the contents of /tmp/cache",
    "Read /tmp/result.txt",
    "Search /tmp/notes.txt for 'todo'",
    "Read and display /tmp/b.txt",
    "Summarize the file at /tmp/config.txt",
    "List files in /tmp/logs",
    "Read /tmp/test.txt",
    "Get the contents of /tmp/old_log.txt",
    "Read the file /tmp/temp.txt",
]


def _conflict_plan(intent, action):
    return {
        "valid": True,
        "plan": {
            "intent": intent,
            "steps": [{"id": "step_1", "action": action, "args": {"path": "/tmp/x"}, "risk": "low"}]
        },
        "attempts": 1,
        "raw_output": "{}"
    }


CONFLICT_CASES = [
    ("Delete the file at /tmp/test.txt using delete_file",
     _conflict_plan("read_only", "read_file")),
    ("Write 'hello' to /tmp/iris_test.txt using write_file",
     _conflict_plan("read_only", "read_file")),
    ("Remove /tmp/old_log.txt using remove_file",
     _conflict_plan("single_action", "write_file")),
    ("Save 'test' to /tmp/output.txt using write_file",
     _conflict_plan("destructive", "delete_file")),
    ("Delete /tmp/cache.txt using delete_file",
     _conflict_plan("single_action", "read_file")),
]


# ── batches ───────────────────────────────────────────────────────────────────

def run_state_change_batch():
    print("\n" + "=" * 60)
    print("BATCH 1 — state-change prompts (slot_2 must trigger + return valid)")
    print("=" * 60)

    results = []
    slot2_called = 0
    slot2_valid  = 0
    agreements   = 0
    conflicts    = 0
    crashes      = 0

    for i, prompt in enumerate(STATE_CHANGE_PROMPTS, 1):
        print(f"\n[{i:02d}] {prompt}")
        try:
            result  = pipeline_run(prompt)
            error   = result.get("error")
            verdict = result.get("verdict")

            slot1_failed       = error in ("invalid_json", "schema_violation", "cannot_plan")
            slot2_fail         = isinstance(error, str) and error.startswith("slot2_")
            actually_triggered = not slot1_failed
            returned_valid     = actually_triggered and not slot2_fail
            agreed             = returned_valid and error != "slot_conflict"

            if actually_triggered:
                slot2_called += 1
            if returned_valid:
                slot2_valid += 1
            if agreed:
                agreements += 1
            if error == "slot_conflict":
                conflicts += 1

            status = ("slot_conflict" if error == "slot_conflict"
                      else f"slot2_fail:{error}" if slot2_fail
                      else f"verdict:{verdict}")
            print(f"    triggered={actually_triggered} valid={returned_valid} agreed={agreed} → {status}")

            results.append({
                "prompt": prompt,
                "slot2_triggered": actually_triggered,
                "slot2_valid": returned_valid,
                "agreed": agreed,
                "verdict": verdict,
                "error": error,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({
                "prompt": prompt,
                "slot2_triggered": False,
                "slot2_valid": False,
                "agreed": False,
                "verdict": None,
                "error": f"exception: {e}",
            })

    return results, slot2_called, slot2_valid, agreements, conflicts, crashes


def run_read_only_batch():
    print("\n" + "=" * 60)
    print("BATCH 2 — read-only prompts (slot_2 must NOT trigger)")
    print("=" * 60)

    results   = []
    triggered = 0
    crashes   = 0

    for i, prompt in enumerate(READ_ONLY_PROMPTS, 1):
        print(f"\n[{i:02d}] {prompt}")
        try:
            result  = pipeline_run(prompt)
            error   = result.get("error")
            fired   = error == "slot_conflict" or (isinstance(error, str) and error.startswith("slot2_"))

            if fired:
                triggered += 1

            print(f"    slot2_triggered={fired} verdict={result.get('verdict')}")
            results.append({
                "prompt": prompt,
                "slot2_triggered": fired,
                "verdict": result.get("verdict"),
                "error": error,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({
                "prompt": prompt,
                "slot2_triggered": False,
                "verdict": None,
                "error": f"exception: {e}",
            })

    return results, triggered, crashes


def run_conflict_batch():
    print("\n" + "=" * 60)
    print("BATCH 3 — mocked conflicts (must surface as structured slot_conflict)")
    print("=" * 60)

    results              = []
    structured_conflicts = 0
    crashes              = 0

    for i, (prompt, mock_plan) in enumerate(CONFLICT_CASES, 1):
        print(f"\n[{i:02d}] {prompt}")
        try:
            with patch("iris.pipeline.slot2_call", return_value=mock_plan):
                result = pipeline_run(prompt)

            error      = result.get("error")
            verdict    = result.get("verdict")
            comparison = result.get("comparison")
            response   = result.get("response")

            is_structured = (
                error == "slot_conflict"
                and verdict == "reject"
                and isinstance(comparison, dict)
                and "conflict" in comparison
                and bool(response)
            )

            if is_structured:
                structured_conflicts += 1

            print(f"    error={error} verdict={verdict} has_comparison={comparison is not None} structured={is_structured}")
            if comparison:
                print(f"    conflict={comparison.get('conflict')}")

            results.append({
                "prompt": prompt,
                "error": error,
                "verdict": verdict,
                "structured": is_structured,
                "comparison": comparison,
                "response": response,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({
                "prompt": prompt,
                "error": f"exception: {e}",
                "verdict": None,
                "structured": False,
                "comparison": None,
                "response": None,
            })

    return results, structured_conflicts, crashes


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sc_results, slot2_called, slot2_valid, agreements, conflicts, sc_crashes = run_state_change_batch()
    ro_results, ro_triggered, ro_crashes = run_read_only_batch()
    cf_results, structured_conflicts, cf_crashes = run_conflict_batch()

    total_sc = len(STATE_CHANGE_PROMPTS)
    total_ro = len(READ_ONLY_PROMPTS)
    total_cf = len(CONFLICT_CASES)

    ec1_pass = slot2_valid == total_sc
    ec2_pass = ro_triggered == 0
    ec3_pass = structured_conflicts == total_cf and cf_crashes == 0

    print("\n" + "=" * 60)
    print("EXIT CRITERIA RESULTS")
    print("=" * 60)

    print(f"\nEC1 — slot_2 called + valid on all state-change prompts:")
    print(f"      slot2_called={slot2_called}/{total_sc}  slot2_valid={slot2_valid}/{total_sc}")
    print(f"      {'PASS' if ec1_pass else 'FAIL'}")

    print(f"\nEC2 — slot_2 never triggered on read-only prompts:")
    print(f"      triggered={ro_triggered}/{total_ro}")
    print(f"      {'PASS' if ec2_pass else 'FAIL'}")

    print(f"\nEC3 — all mocked conflicts surfaced as structured slot_conflict:")
    print(f"      structured={structured_conflicts}/{total_cf}  crashes={cf_crashes}")
    print(f"      {'PASS' if ec3_pass else 'FAIL'}")

    print(f"\n── agreement rate (data only — not a gate) ──────────────────")
    print(f"   agreements={agreements}  conflicts={conflicts}  slot2_valid={slot2_valid}")
    if slot2_valid > 0:
        rate = 100 * agreements // slot2_valid
        print(f"   agreement rate: {agreements}/{slot2_valid} ({rate}%)")

    all_pass = ec1_pass and ec2_pass and ec3_pass
    print("\n" + "=" * 60)
    print(f"Phase 3 exit criteria: {'ALL PASS' if all_pass else 'FAIL — see above'}")
    print("=" * 60 + "\n")

    output = {
        "exit_criteria": {
            "ec1_slot2_valid_on_all_state_change": {
                "pass": ec1_pass,
                "slot2_called": slot2_called,
                "slot2_valid": slot2_valid,
                "total": total_sc
            },
            "ec2_slot2_never_triggered_on_read_only": {
                "pass": ec2_pass,
                "triggered": ro_triggered,
                "total": total_ro
            },
            "ec3_all_conflicts_structured": {
                "pass": ec3_pass,
                "structured": structured_conflicts,
                "total": total_cf,
                "crashes": cf_crashes
            },
        },
        "agreement_rate": {
            "agreements": agreements,
            "conflicts": conflicts,
            "slot2_valid": slot2_valid,
            "rate_pct": (100 * agreements // slot2_valid) if slot2_valid > 0 else None,
        },
        "state_change_results": sc_results,
        "read_only_results":    ro_results,
        "conflict_results":     cf_results,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Results saved to {RESULTS_PATH}\n")
    sys.exit(0 if all_pass else 1)
