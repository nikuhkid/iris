"""
tests/smoke_test.py

Full-stack sanity check for IRIS.
Run at the start of any session to confirm all components are intact.

Covers:
    - validator:             all 4 return cases
    - plan_analysis_initial: operation flags per action type
    - plan_analysis_final:   implicit destructive gate (fires / does not fire)
    - decision_engine:       rule priority order
    - response_model_stub:   authority language absent from all verdicts
    - pipeline (end-to-end): all outcome paths via live model call
    - logger:                row created, fields populated, run_id unique, JSON fields valid

Usage:
    cd /home/nikuhkid/iris && python3 tests/smoke_test.py

Output:
    PASS / FAIL per assertion.
    Summary line at end.
    Exits 0 on all pass, 1 on any failure.
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from iris.validator import validate
from iris.plan_analysis_initial import analyze as pass1
from iris.plan_analysis_final import analyze as pass2
from iris.decision_engine import decide
from iris.response_model_stub import respond
from iris import logger
from iris.pipeline import run as pipeline_run
from iris.comparator import compare

DB_PATH = ROOT / "iris.db"

# ── test harness ──────────────────────────────────────────────────────────────
_results = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── validator ─────────────────────────────────────────────────────────────────
section("validator — all 4 return cases")

valid_plan = json.dumps({
    "intent": "read_only",
    "steps": [{"id": "step_read", "action": "read_file", "args": {"path": "/tmp/test.txt"}, "risk": "low"}]
})
r = validate(valid_plan)
check("valid plan → valid=True",             r["valid"] is True)
check("valid plan → plan key present",       "plan" in r)

r = validate("this is not json at all")
check("invalid JSON → valid=False",          r["valid"] is False)
check("invalid JSON → error=invalid_json",   r.get("error") == "invalid_json")

bad_intent = json.dumps({
    "intent": "do_something_weird",
    "steps": [{"id": "step_1", "action": "read_file", "args": {}, "risk": "low"}]
})
r = validate(bad_intent)
check("schema violation → valid=False",            r["valid"] is False)
check("schema violation → error=schema_violation", r.get("error") == "schema_violation")

cannot = json.dumps({"error": "cannot_plan", "reason": "too ambiguous"})
r = validate(cannot)
check("cannot_plan → valid=False",           r["valid"] is False)
check("cannot_plan → error=cannot_plan",     r.get("error") == "cannot_plan")
check("cannot_plan → reason preserved",      r.get("reason") == "too ambiguous")

# ── plan_analysis_initial ─────────────────────────────────────────────────────
section("plan_analysis_initial — operation flags")

read_plan = {
    "intent": "read_only",
    "steps": [{"id": "step_1", "action": "read_file", "args": {"path": "/tmp/f"}, "risk": "low"}]
}
a = pass1(read_plan)
check("read_file → read=True",    a["operation_flags"]["read"]["value"]   is True)
check("read_file → write=False",  a["operation_flags"]["write"]["value"]  is False)
check("read_file → delete=False", a["operation_flags"]["delete"]["value"] is False)

write_plan = {
    "intent": "single_action",
    "steps": [{"id": "step_1", "action": "write_file", "args": {"path": "/tmp/f"}, "risk": "medium"}]
}
a = pass1(write_plan)
check("write_file → write=True",  a["operation_flags"]["write"]["value"]  is True)
check("write_file → read=False",  a["operation_flags"]["read"]["value"]   is False)

delete_plan = {
    "intent": "destructive",
    "steps": [{"id": "step_1", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"}]
}
a = pass1(delete_plan)
check("delete_file → delete=True", a["operation_flags"]["delete"]["value"] is True)

unknown_plan = {
    "intent": "single_action",
    "steps": [{"id": "step_1", "action": "launch_missiles", "args": {}, "risk": "high"}]
}
a = pass1(unknown_plan)
check("unknown action → unknown=True", a["operation_flags"]["unknown"]["value"] is True)

multi_plan = {
    "intent": "multi_step",
    "steps": [
        {"id": "step_1", "action": "read_file",  "args": {"path": "/tmp/a"}, "risk": "low"},
        {"id": "step_2", "action": "write_file", "args": {"path": "/tmp/b"}, "risk": "medium"},
    ]
}
a = pass1(multi_plan)
check("multi-step → multi_step=True",   a["state_flags"]["multi_step"]["value"]   is True)
check("multi-step → state_change=True", a["state_flags"]["state_change"]["value"] is True)

# ── plan_analysis_final ───────────────────────────────────────────────────────
section("plan_analysis_final — implicit destructive gate")

# Should NOT fire: read-only, term appears only in filename
safe_plan = {
    "intent": "read_only",
    "steps": [{"id": "s", "action": "read_file", "args": {"path": "/tmp/clear_notes.txt"}, "risk": "low"}]
}
p1 = pass1(safe_plan)
p2 = pass2(p1, safe_plan)
check("clean read → implicit_destructive=False",
      p2["intent_signals"]["implicit_destructive"]["value"] is False)

# Should NOT fire: lexical hit injected but no write/delete scope — scope gate blocks it
commentary_plan = {
    "intent": "read_only",
    "steps": [{"id": "s", "action": "read_file", "args": {"path": "/tmp/test.txt"}, "risk": "low"}]
}
p1 = pass1(commentary_plan)
p1["raw_terms_detected"]["value"].append("purge")
p2 = pass2(p1, commentary_plan)
check("lexical hit, no write/delete scope → implicit_destructive=False",
      p2["intent_signals"]["implicit_destructive"]["value"] is False)

# Should fire: purge in action + args, delete scope active
purge_plan = {
    "intent": "single_action",
    "steps": [{"id": "s", "action": "purge", "args": {"target": "/tmp/logs"}, "risk": "high"}]
}
p1 = pass1(purge_plan)
p2 = pass2(p1, purge_plan)
check("purge in args + delete scope → implicit_destructive=True",
      p2["intent_signals"]["implicit_destructive"]["value"] is True)

# purge_plan has intent=single_action — explicit_destructive should be False
# (explicit_destructive fires only when intent field == "destructive")
check("purge/single_action → explicit_destructive=False",
      p2["intent_signals"]["explicit_destructive"]["value"] is False)

# explicit_destructive fires when intent field == "destructive"
explicit_plan = {
    "intent": "destructive",
    "steps": [{"id": "s", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"}]
}
p1_ex = pass1(explicit_plan)
p2_ex = pass2(p1_ex, explicit_plan)
check("intent=destructive → explicit_destructive=True",
      p2_ex["intent_signals"]["explicit_destructive"]["value"] is True)

# Pass 1 facts must be immutable in pass 2 output
check("pass 1 flags immutable in pass 2",
      p2["operation_flags"] == p1["operation_flags"])

# ── decision_engine ───────────────────────────────────────────────────────────
section("decision_engine — rule priority and verdicts")


def _decide(plan):
    return decide(pass2(pass1(plan), plan), plan)


r = _decide(read_plan)
check("clean read → proceed",               r["verdict"] == "proceed")

r = _decide(delete_plan)
check("delete_file → require_confirmation", r["verdict"] == "require_confirmation")

r = _decide(unknown_plan)
check("unknown action → reject",            r["verdict"] == "reject")

r = _decide(purge_plan)
check("purge (irreversible) → require_confirmation", r["verdict"] == "require_confirmation")

# Rule priority: unknown fires before irreversible
mixed_plan = {
    "intent": "single_action",
    "steps": [
        {"id": "s1", "action": "launch_missiles", "args": {}, "risk": "high"},
        {"id": "s2", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"},
    ]
}
r = _decide(mixed_plan)
check("unknown + irreversible → reject (unknown fires first)", r["verdict"] == "reject")

# ── response_model_stub ───────────────────────────────────────────────────────
section("response_model_stub — no authority language")

AUTHORITY_PHRASES = [
    "i executed", "i ran", "i deleted", "i wrote",
    "i enforced", "i decided", "i performed", "i completed"
]

for verdict_str, plan in [
    ("proceed",              read_plan),
    ("require_confirmation", delete_plan),
    ("reject",               unknown_plan),
]:
    verdict_dict = {"verdict": verdict_str, "reason": "test reason"}
    r = respond(verdict_dict, plan)
    response_lower = r["response"].lower()
    hit = next((p for p in AUTHORITY_PHRASES if p in response_lower), None)
    check(f"{verdict_str} → no authority language", hit is None,
          f"found: '{hit}'" if hit else "")

# ── logger ────────────────────────────────────────────────────────────────────
section("logger — row creation, field population, uniqueness")

logger.init_db()

try:
    logger.init_db()
    check("init_db idempotent (double call)", True)
except Exception as e:
    check("init_db idempotent (double call)", False, str(e))

run_id = logger.start_run(source="cli", user_id=None)
try:
    uuid.UUID(run_id)
    check("start_run returns valid UUID", True)
except ValueError:
    check("start_run returns valid UUID", False, f"got: {run_id}")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM pipeline_log WHERE run_id = ?", (run_id,)).fetchone()
check("row exists after start_run", row is not None)
check("timestamp populated",        bool(row["timestamp"]) if row else False)
check("source=cli",                 row["source"] == "cli" if row else False)
check("user_id=None",               row["user_id"] is None if row else False)

logger.update_run(
    run_id,
    raw_input="test input",
    guard_passed=1,
    verdict="proceed",
    verdict_reason="no blocking rules",
    plan_json={"intent": "read_only", "steps": []},
)
row = conn.execute("SELECT * FROM pipeline_log WHERE run_id = ?", (run_id,)).fetchone()
check("raw_input written",      row["raw_input"] == "test input" if row else False)
check("guard_passed=1",         row["guard_passed"] == 1 if row else False)
check("verdict=proceed",        row["verdict"] == "proceed" if row else False)
check("plan_json deserializable",
      False if not row else isinstance(json.loads(row["plan_json"]), dict))

check("previous_hash is NULL",  row["previous_hash"] is None if row else False)
check("entry_hash is NULL",     row["entry_hash"] is None if row else False)
check("sequence_gap=0",         row["sequence_gap"] == 0 if row else False)

try:
    logger.update_run(run_id, nonexistent_column="boom")
    check("unknown column raises ValueError", False, "no exception raised")
except ValueError:
    check("unknown column raises ValueError", True)

run_id_2 = logger.start_run()
check("consecutive run_ids are unique", run_id != run_id_2)

conn.close()

# ── comparator ───────────────────────────────────────────────────────────────
section("comparator — match and mismatch cases")

_plan_a = {
    "intent": "destructive",
    "steps": [
        {"id": "s1", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"},
        {"id": "s2", "action": "read_file",   "args": {"path": "/tmp/g"}, "risk": "low"},
    ]
}
_plan_b = {
    "intent": "destructive",
    "steps": [
        {"id": "s1", "action": "read_file",   "args": {"path": "/tmp/g"}, "risk": "low"},
        {"id": "s2", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"},
    ]
}
_plan_c = {
    "intent": "read_only",
    "steps": [
        {"id": "s1", "action": "delete_file", "args": {"path": "/tmp/f"}, "risk": "high"},
    ]
}
_plan_d = {
    "intent": "destructive",
    "steps": [
        {"id": "s1", "action": "remove_file", "args": {"path": "/tmp/f"}, "risk": "high"},
    ]
}

r = compare(_plan_a, _plan_b)
check("same intent + actions (different order) → match=True",  r["match"] is True)
check("match → conflict=None",                                  r["conflict"] is None)

r = compare(_plan_a, _plan_c)
check("intent mismatch → match=False",                          r["match"] is False)
check("intent mismatch → intent_mismatch=True",                 r["conflict"]["intent_mismatch"] is True)
check("intent mismatch → actions_mismatch=True",                r["conflict"]["actions_mismatch"] is True)

r = compare(_plan_a, _plan_d)
check("delete_file vs remove_file → match=False (dumb comparator)", r["match"] is False)
check("delete_file vs remove_file → actions_mismatch=True",         r["conflict"]["actions_mismatch"] is True)
check("delete_file vs remove_file → intent_mismatch=False",         r["conflict"]["intent_mismatch"] is False)

# ── pipeline end-to-end (live model) ─────────────────────────────────────────
section("pipeline — end-to-end (live model call)")
print("  note: requires Ollama running with iris-slot1\n")

cases = [
    ("clean read",
     "Read the file at /tmp/test.txt and summarize its contents",
     "proceed"),
    ("cannot_plan",
     "save this",
     "reject"),
]

for label, user_input, expected_verdict in cases:
    try:
        with patch("iris.pipeline.approval_prompt", return_value={"action": "approve"}):
            result = pipeline_run(user_input)
        verdict_ok  = result["verdict"] == expected_verdict
        run_id_ok   = bool(result.get("run_id"))
        response_ok = bool(result.get("response"))

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        db_row = conn.execute(
            "SELECT verdict, raw_input, response FROM pipeline_log WHERE run_id = ?",
            (result["run_id"],)
        ).fetchone()
        conn.close()

        db_verdict_ok  = db_row["verdict"] == expected_verdict if db_row else False
        db_response_ok = bool(db_row["response"]) if db_row else False

        check(f"{label} → verdict={expected_verdict}", verdict_ok)
        check(f"{label} → run_id in result",           run_id_ok)
        check(f"{label} → response non-empty",         response_ok)
        check(f"{label} → DB verdict matches",         db_verdict_ok)
        check(f"{label} → DB response written",        db_response_ok)

    except Exception as e:
        check(f"{label} → pipeline completed without exception", False, str(e))

# ── Phase 3 — slot_2 + comparator ────────────────────────────────────────────
section("pipeline — Phase 3 slot_2 + comparator (live + mocked)")
print("  note: case 1 requires Ollama running with iris-slot1 and iris-slot2\n")

# Case 1 — live: state_change triggers slot_2, both slots agree → proceed to verdict
# Prompt nudges model toward write_file to avoid vocabulary drift failing the test
try:
    with patch("iris.pipeline.approval_prompt", return_value={"action": "approve"}):
        result = pipeline_run("Write 'hello' to /tmp/iris_test.txt using write_file")
    # state_change must have triggered — if unknown gate fired instead, verdict is reject
    # and slot_2 was never called. Check error field to distinguish.
    slot2_triggered = result.get("error") != "slot_conflict" and result.get("verdict") in ("proceed", "require_confirmation", "reject")
    not_slot_conflict = result.get("error") != "slot_conflict"
    run_id_ok   = bool(result.get("run_id"))
    response_ok = bool(result.get("response"))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db_row = conn.execute(
        "SELECT verdict, slot_used, response FROM pipeline_log WHERE run_id = ?",
        (result["run_id"],)
    ).fetchone()
    conn.close()

    # slot_used=2 confirms slot_2 was triggered and logged
    slot_used_ok   = db_row["slot_used"] == 2 if db_row else False
    db_response_ok = bool(db_row["response"]) if db_row else False

    check("slot_2 live — slot_used=2 logged",         slot_used_ok)
    check("slot_2 live — no slot_conflict error",      not_slot_conflict)
    check("slot_2 live — run_id in result",            run_id_ok)
    check("slot_2 live — response non-empty",          response_ok)
    check("slot_2 live — DB response written",         db_response_ok)

except Exception as e:
    check("slot_2 live — pipeline completed without exception", False, str(e))

# Case 2 — mocked: slot_2 returns invalid plan → pipeline rejects with slot2_* error
_invalid_slot2 = {"valid": False, "error": "schema_violation", "detail": "missing intent", "attempts": 3, "raw_output": "{}"}

with patch("iris.pipeline.slot2_call", return_value=_invalid_slot2):
    try:
        result = pipeline_run("Delete the file at /tmp/test.txt")
        check("slot_2 failure — verdict=reject",          result["verdict"] == "reject")
        check("slot_2 failure — error starts slot2_",     (result.get("error") or "").startswith("slot2_"))
        check("slot_2 failure — response non-empty",      bool(result.get("response")))
    except Exception as e:
        check("slot_2 failure — pipeline completed without exception", False, str(e))

# Case 3 — mocked: slot_2 returns valid but different plan → slot_conflict
_conflict_plan = {
    "valid": True,
    "plan": {
        "intent": "read_only",         # slot_1 will produce destructive/single_action
        "steps": [
            {"id": "step_1", "action": "read_file", "args": {"path": "/tmp/test.txt"}, "risk": "low"}
        ]
    },
    "attempts": 1,
    "raw_output": '{"intent": "read_only", "steps": [...]}'
}

with patch("iris.pipeline.slot2_call", return_value=_conflict_plan):
    try:
        result = pipeline_run("Delete the file at /tmp/test.txt")
        check("slot_conflict — verdict=reject",       result["verdict"] == "reject")
        check("slot_conflict — error=slot_conflict",  result.get("error") == "slot_conflict")
        check("slot_conflict — response non-empty",   bool(result.get("response")))
        check("slot_conflict — comparison in result", "comparison" in result)
    except Exception as e:
        check("slot_conflict — pipeline completed without exception", False, str(e))

# ── observer — seal and hash chain ───────────────────────────────────────────
section("observer — seal and hash chain")

from iris import observer as obs
import importlib

# Reset module-level buffer/degraded state between test runs
obs._buffer.clear()
obs._degraded = False

# Seal two fresh runs and verify hash fields are populated
logger.init_db()
rid_a = logger.start_run(source="cli")
logger.update_run(rid_a, raw_input="observer test A", guard_passed=1, verdict="proceed")
seal_ok_a = obs.seal_run(rid_a)
check("seal_run returns True on success", seal_ok_a is True)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
row_a = conn.execute("SELECT * FROM pipeline_log WHERE run_id = ?", (rid_a,)).fetchone()
check("entry_hash populated after seal",   bool(row_a["entry_hash"]))
check("previous_hash = GENESIS (first row or chain start)",
      row_a["previous_hash"] in ("GENESIS",) or row_a["previous_hash"].startswith("UNSEALED") or len(row_a["previous_hash"]) == 64)
check("sequence_gap=0 after clean seal",   row_a["sequence_gap"] == 0)

rid_b = logger.start_run(source="cli")
logger.update_run(rid_b, raw_input="observer test B", guard_passed=1, verdict="proceed")
seal_ok_b = obs.seal_run(rid_b)
check("seal_run second row returns True",  seal_ok_b is True)

row_b = conn.execute("SELECT * FROM pipeline_log WHERE run_id = ?", (rid_b,)).fetchone()
check("second row previous_hash = first row entry_hash",
      row_b["previous_hash"] == row_a["entry_hash"])
check("second row entry_hash populated",   bool(row_b["entry_hash"]))

# ── observer — verify_chain ───────────────────────────────────────────────────
section("observer — verify_chain")

violations = obs.verify_chain()
unsealed_in_chain = [v for v in violations if v["violation_type"] == "unsealed"]
mismatch_in_chain = [v for v in violations if v["violation_type"] == "hash_mismatch"]
# There may be pre-existing unsealed rows from earlier test cases — that's expected.
# The two rows we just sealed should not appear as hash_mismatch.
our_run_ids = {rid_a, rid_b}
our_mismatches = [v for v in mismatch_in_chain if v["run_id"] in our_run_ids]
check("sealed rows have no hash_mismatch violations", len(our_mismatches) == 0)

# Seal a third row so there's a downstream row to break when we corrupt rid_b
rid_c_chain = logger.start_run(source="cli")
logger.update_run(rid_c_chain, raw_input="observer test C chain", guard_passed=1, verdict="proceed")
obs.seal_run(rid_c_chain)

# Corrupt rid_b and confirm both hash_mismatch and downstream chain_break surface
correct_entry_hash_b = row_b["entry_hash"]
conn.execute(
    "UPDATE pipeline_log SET entry_hash = 'deadbeef' WHERE run_id = ?", (rid_b,)
)
conn.commit()
violations_after = obs.verify_chain()
corrupted = [v for v in violations_after if v["run_id"] == rid_b]
has_mismatch = any(v["violation_type"] == "hash_mismatch" for v in corrupted)
check("corrupted entry_hash detected as hash_mismatch", has_mismatch)
downstream = [v for v in violations_after if v["run_id"] == rid_c_chain]
has_chain_break = any(v["violation_type"] == "chain_break" for v in downstream)
check("corruption causes downstream chain_break", has_chain_break)

# Restore correct hash so the rest of the chain is sound
conn.execute(
    "UPDATE pipeline_log SET entry_hash = ? WHERE run_id = ?", (correct_entry_hash_b, rid_b)
)
conn.commit()
conn.close()

# ── observer — degraded mode and replay ───────────────────────────────────────
section("observer — degraded mode and replay")

obs._buffer.clear()
obs._degraded = False

# Force a seal failure by passing a nonexistent run_id
fake_id = str(uuid.uuid4())
result_fail = obs.seal_run(fake_id)
check("seal_run returns False on failure",    result_fail is False)
check("failed run_id appended to _buffer",    fake_id in obs._buffer)
check("_degraded set to True after failure",  obs._degraded is True)

# Now add a real run_id to the buffer manually and replay
rid_c = logger.start_run(source="cli")
logger.update_run(rid_c, raw_input="observer test C", guard_passed=1, verdict="proceed")
obs._buffer.append(rid_c)

replay_result = obs.replay_buffer()
check("replay_buffer seals the real run_id",  replay_result["sealed"] >= 1)
check("nonexistent id remains in buffer",      fake_id in obs._buffer)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
row_c = conn.execute("SELECT * FROM pipeline_log WHERE run_id = ?", (rid_c,)).fetchone()
check("replayed row has entry_hash populated", bool(row_c["entry_hash"]))
check("replayed row has sequence_gap=1",       row_c["sequence_gap"] == 1)
conn.close()

# ── summary ───────────────────────────────────────────────────────────────────
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed

print(f"\n{'═' * 60}")
print(f"  SMOKE TEST COMPLETE — {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    print(f"\n  Failed checks:")
    for label, ok in _results:
        if not ok:
            print(f"    ✗ {label}")
else:
    print("  ✓ all green")
print(f"{'═' * 60}\n")

sys.exit(0 if failed == 0 else 1)
