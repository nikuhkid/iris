"""
tests/exit_criteria_p4.py

Phase 4 exit criteria test.

Exit criteria (pass/fail gates):
    EC1 — no execution without approval:
          all approved runs return user_action=approve, error=None, user_action logged in DB
    EC2 — reject/kill stops cleanly:
          all rejected/killed runs return correct user_action, no crash, logged in DB
    EC3 — modify returns amended_input to caller:
          all modify runs return amended_input in result, user_action=modify logged in DB

Results saved to tests/exit_criteria_p4_results.json.

Usage:
    cd /home/nikuhkid/iris && python3 tests/exit_criteria_p4.py
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from iris.pipeline import run as pipeline_run

RESULTS_PATH = ROOT / "tests" / "exit_criteria_p4_results.json"
DB_PATH      = ROOT / "iris.db"


def _db_user_action(run_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT user_action FROM pipeline_log WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return row["user_action"] if row else None


# ── prompts ───────────────────────────────────────────────────────────────────

APPROVE_PROMPTS = [
    "Read the file at /tmp/test.txt and summarize its contents",
    "List the files in /tmp",
    "Write 'hello' to /tmp/iris_test.txt using write_file",
    "Delete the file at /tmp/test.txt using delete_file",
    "Append 'entry' to /tmp/log.txt using append_file",
    "Read /tmp/config.txt",
    "Save 'done' to /tmp/status.txt using write_file",
    "Remove /tmp/old_log.txt using remove_file",
    "Summarize the contents of /tmp/notes.txt",
    "Write 'debug=true' to /tmp/config.txt using write_file",
]

REJECT_KILL_PROMPTS = [
    ("Read the file at /tmp/test.txt",                           "reject"),
    ("Write 'hello' to /tmp/iris_test.txt using write_file",     "reject"),
    ("Delete the file at /tmp/test.txt using delete_file",       "reject"),
    ("Append 'entry' to /tmp/log.txt using append_file",         "kill"),
    ("Save 'done' to /tmp/status.txt using write_file",          "kill"),
    ("Remove /tmp/old_log.txt using remove_file",                "kill"),
    ("Summarize /tmp/notes.txt",                                 "reject"),
    ("Write 'debug=true' to /tmp/config.txt using write_file",   "kill"),
    ("Read /tmp/config.txt",                                     "reject"),
    ("List the files in /tmp",                                   "kill"),
]

MODIFY_CASES = [
    ("Read this",           "Read the file at /tmp/test.txt"),
    ("Delete that",         "Delete the file at /tmp/test.txt using delete_file"),
    ("Save it",             "Save 'ok' to /tmp/result.txt using write_file"),
    ("Update the thing",    "Write 'updated' to /tmp/notes.txt using write_file"),
    ("Clean up",            "Delete the file at /tmp/temp.txt using delete_file"),
]


# ── batches ───────────────────────────────────────────────────────────────────

def run_approve_batch():
    print("\n" + "=" * 60)
    print("BATCH 1 — approve (all runs must reach user_action=approve)")
    print("=" * 60)

    results  = []
    passed   = 0
    crashes  = 0

    for i, prompt in enumerate(APPROVE_PROMPTS, 1):
        print(f"\n[{i:02d}] {prompt}")
        try:
            with patch("iris.pipeline.approval_prompt", return_value={"action": "approve"}):
                result = pipeline_run(prompt)

            user_action  = result.get("user_action")
            error        = result.get("error")
            run_id       = result.get("run_id")
            db_action    = _db_user_action(run_id) if run_id else None

            ok = (
                user_action == "approve"
                and error is None
                and db_action == "approve"
            )
            if ok:
                passed += 1

            print(f"    user_action={user_action} error={error} db_action={db_action} → {'PASS' if ok else 'FAIL'}")
            results.append({
                "prompt": prompt,
                "user_action": user_action,
                "error": error,
                "db_action": db_action,
                "pass": ok,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({"prompt": prompt, "error": f"exception: {e}", "pass": False})

    return results, passed, crashes


def run_reject_kill_batch():
    print("\n" + "=" * 60)
    print("BATCH 2 — reject/kill (pipeline must stop cleanly, action logged)")
    print("=" * 60)

    results  = []
    passed   = 0
    crashes  = 0

    for i, (prompt, action) in enumerate(REJECT_KILL_PROMPTS, 1):
        print(f"\n[{i:02d}] [{action}] {prompt}")
        try:
            with patch("iris.pipeline.approval_prompt", return_value={"action": action}):
                result = pipeline_run(prompt)

            user_action  = result.get("user_action")
            error        = result.get("error")
            response     = result.get("response")
            run_id       = result.get("run_id")
            db_action    = _db_user_action(run_id) if run_id else None

            ok = (
                user_action == action
                and error is None
                and bool(response)
                and db_action == action
            )
            if ok:
                passed += 1

            print(f"    user_action={user_action} error={error} db_action={db_action} → {'PASS' if ok else 'FAIL'}")
            results.append({
                "prompt": prompt,
                "expected_action": action,
                "user_action": user_action,
                "error": error,
                "db_action": db_action,
                "pass": ok,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({"prompt": prompt, "expected_action": action, "error": f"exception: {e}", "pass": False})

    return results, passed, crashes


def run_modify_batch():
    print("\n" + "=" * 60)
    print("BATCH 3 — modify (amended_input must be returned to caller)")
    print("=" * 60)

    results  = []
    passed   = 0
    crashes  = 0

    for i, (prompt, amended) in enumerate(MODIFY_CASES, 1):
        print(f"\n[{i:02d}] {prompt!r} → amended: {amended!r}")
        try:
            with patch("iris.pipeline.approval_prompt",
                       return_value={"action": "modify", "amended_input": amended}):
                result = pipeline_run(prompt)

            user_action   = result.get("user_action")
            amended_back  = result.get("amended_input")
            error         = result.get("error")
            run_id        = result.get("run_id")
            db_action     = _db_user_action(run_id) if run_id else None

            ok = (
                user_action == "modify"
                and amended_back == amended
                and error is None
                and db_action == "modify"
            )
            if ok:
                passed += 1

            print(f"    user_action={user_action} amended_input={amended_back!r} db_action={db_action} → {'PASS' if ok else 'FAIL'}")
            results.append({
                "prompt": prompt,
                "amended_input": amended_back,
                "user_action": user_action,
                "error": error,
                "db_action": db_action,
                "pass": ok,
            })

        except Exception as e:
            crashes += 1
            print(f"    CRASH: {e}")
            results.append({"prompt": prompt, "error": f"exception: {e}", "pass": False})

    return results, passed, crashes


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap_results, ap_passed, ap_crashes = run_approve_batch()
    rk_results, rk_passed, rk_crashes = run_reject_kill_batch()
    mo_results, mo_passed, mo_crashes = run_modify_batch()

    total_ap = len(APPROVE_PROMPTS)
    total_rk = len(REJECT_KILL_PROMPTS)
    total_mo = len(MODIFY_CASES)

    ec1_pass = ap_passed == total_ap and ap_crashes == 0
    ec2_pass = rk_passed == total_rk and rk_crashes == 0
    ec3_pass = mo_passed == total_mo and mo_crashes == 0

    print("\n" + "=" * 60)
    print("EXIT CRITERIA RESULTS")
    print("=" * 60)

    print(f"\nEC1 — no execution without approval:")
    print(f"      passed={ap_passed}/{total_ap}  crashes={ap_crashes}")
    print(f"      {'PASS' if ec1_pass else 'FAIL'}")

    print(f"\nEC2 — reject/kill stops cleanly:")
    print(f"      passed={rk_passed}/{total_rk}  crashes={rk_crashes}")
    print(f"      {'PASS' if ec2_pass else 'FAIL'}")

    print(f"\nEC3 — modify returns amended_input to caller:")
    print(f"      passed={mo_passed}/{total_mo}  crashes={mo_crashes}")
    print(f"      {'PASS' if ec3_pass else 'FAIL'}")

    all_pass = ec1_pass and ec2_pass and ec3_pass
    print("\n" + "=" * 60)
    print(f"Phase 4 exit criteria: {'ALL PASS' if all_pass else 'FAIL — see above'}")
    print("=" * 60 + "\n")

    output = {
        "exit_criteria": {
            "ec1_approve_gate": {
                "pass": ec1_pass,
                "passed": ap_passed,
                "total": total_ap,
                "crashes": ap_crashes,
            },
            "ec2_reject_kill_clean": {
                "pass": ec2_pass,
                "passed": rk_passed,
                "total": total_rk,
                "crashes": rk_crashes,
            },
            "ec3_modify_returns_amended_input": {
                "pass": ec3_pass,
                "passed": mo_passed,
                "total": total_mo,
                "crashes": mo_crashes,
            },
        },
        "approve_results":      ap_results,
        "reject_kill_results":  rk_results,
        "modify_results":       mo_results,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Results saved to {RESULTS_PATH}\n")
    sys.exit(0 if all_pass else 1)
