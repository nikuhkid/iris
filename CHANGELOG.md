# IRIS Changelog

---

## Session 1 — 2026-04-09

### Environment Setup

**Installed Ollama 0.20.4**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

**Pulled base model**
```bash
ollama pull qwen2.5:7b-instruct-q4_K_M
```

**Created Modelfiles and built named models**
```bash
# Modelfile.slot1 — temp 0.2, ctx 2048
# Modelfile.slot2 — temp 0.3, ctx 2048
cd /home/nikuhkid/iris && ollama create iris-slot1 -f Modelfile.slot1 && ollama create iris-slot2 -f Modelfile.slot2
ollama list
```

**Smoke tested both slots**
```bash
curl -s http://localhost:11434/api/chat -d '{
  "model": "iris-slot1",
  "stream": false,
  "messages": [{"role": "user", "content": "Generate a plan to read a file at /tmp/test.txt. Output only valid JSON with fields: intent, steps (array of objects with id, action, args, risk)."}]
}'

curl -s http://localhost:11434/api/chat -d '{
  "model": "iris-slot2",
  "stream": false,
  "messages": [{"role": "user", "content": "Generate a plan to read a file at /tmp/test.txt. Output only valid JSON with fields: intent, steps (array of objects with id, action, args, risk)."}]
}'
```

**Verified concurrent VRAM usage**
```bash
curl -s http://localhost:11434/api/chat -d '{"model":"iris-slot1","stream":false,"messages":[{"role":"user","content":"Generate a plan to read a file at /tmp/test.txt. Output only valid JSON with fields: intent, steps (array of objects with id, action, args, risk)."}]}' &
curl -s http://localhost:11434/api/chat -d '{"model":"iris-slot2","stream":false,"messages":[{"role":"user","content":"Generate a plan to read a file at /tmp/test.txt. Output only valid JSON with fields: intent, steps (array of objects with id, action, args, risk)."}]}' &
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv -l 1 &
wait
```
Result: 4721 MiB used — single footprint confirmed, shared weights working.

---

### Phase 1 — Skeleton

#### Task 1 — Plan Schema (`schema/plan.json`)
Decisions locked:
- `intent` — strict enum: `single_action | multi_step | read_only | destructive`. Hard fail on anything outside.
- `args` — free object for Phase 1. Type per action in Phase 2 based on observed data.
- `id` — string, pattern `step_[a-z0-9_]+`. No integers, no sequential assumption.

#### Task 2 — Planning System Prompt (`prompts/planning_system.txt`)
- Role declaration, hard output constraint, schema inline.
- Failure output is structured JSON: `{"error": "cannot_plan", "reason": "..."}` — validator always receives JSON regardless of outcome.
- Prompt loaded at runtime, not hardcoded.

**Tested prompt via Python (correct method — handles newlines in prompt file):**
```bash
python3 -c "
import json, urllib.request

prompt = open('/home/nikuhkid/iris/prompts/planning_system.txt').read()

payload = {
    'model': 'iris-slot1',
    'stream': False,
    'messages': [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': 'Read the file at /tmp/test.txt and summarize its contents.'}
    ]
}

req = urllib.request.Request(
    'http://localhost:11434/api/chat',
    data=json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json'}
)

resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
print(data['message']['content'])
"
```
Result: valid JSON, correct schema, `step_read_file` / `step_summarize_content` IDs, zero natural language outside JSON.
Note: model produced `{{ step_read_file.result }}` as step chaining reference — not a schema violation (free args object), worth noting for Phase 4 orchestrator design.

#### Task 3 — Schema Validator (`iris/validator.py`)
Four return cases: `valid`, `invalid_json`, `schema_violation`, `cannot_plan`.
Never raises — always returns structured result.

**Tested:**
```bash
cd /home/nikuhkid/iris && python3 -c "
from iris.validator import validate
import json

valid = json.dumps({'intent': 'read_only', 'steps': [{'id': 'step_read_file', 'action': 'read_file', 'args': {'path': '/tmp/test.txt'}, 'risk': 'low'}]})
print('Case 1 (valid):', validate(valid))

print('Case 2 (invalid JSON):', validate('this is not json'))

bad_intent = json.dumps({'intent': 'do_something', 'steps': [{'id': 'step_1', 'action': 'read_file', 'args': {}, 'risk': 'low'}]})
print('Case 3 (schema violation):', validate(bad_intent))

cannot = json.dumps({'error': 'cannot_plan', 'reason': 'request is ambiguous'})
print('Case 4 (cannot_plan):', validate(cannot))
"
```

#### Task 4 — plan_analysis_initial (`iris/plan_analysis_initial.py`)
Action vocabulary in `config/action_map.json` — configurable, not hardcoded.
Unknown actions flagged as `operation_flags.unknown: true` — load-bearing, decision engine rejects.
`summarize_text` classified as read — does not mutate state. Edge case noted: revisit if it appears with write-type args in Phase 2.

**Tested:**
```bash
cd /home/nikuhkid/iris && python3 -c "
from iris.plan_analysis_initial import analyze
import json

plan = {
    'intent': 'read_only',
    'steps': [
        {'id': 'step_read_file', 'action': 'read_file', 'args': {'path': '/tmp/test.txt'}, 'risk': 'low'},
        {'id': 'step_summarize_content', 'action': 'summarize_text', 'args': {'text': '{{ step_read_file.result }}'}, 'risk': 'low'}
    ]
}
print(json.dumps(analyze(plan), indent=2))
"
```

#### Task 5 — plan_analysis_final (`iris/plan_analysis_final.py`)
Three-step implicit destructive gate:
1. Lexical match against term list
2. Scope gate — write OR delete must be true
3. Term must appear in action strings or args — not commentary

Facts from pass 1 are immutable — pass 2 only appends `intent_signals`.

**Tested:**
```bash
cd /home/nikuhkid/iris && python3 -c "
from iris.plan_analysis_initial import analyze as pass1
from iris.plan_analysis_final import analyze as pass2
import json

plan1 = {'intent': 'read_only', 'steps': [{'id': 'step_read', 'action': 'read_file', 'args': {'path': '/tmp/test.txt'}, 'risk': 'low'}]}
plan2 = {'intent': 'single_action', 'steps': [{'id': 'step_purge', 'action': 'purge', 'args': {'target': '/tmp/logs'}, 'risk': 'high'}]}
plan3 = {'intent': 'read_only', 'steps': [{'id': 'step_read', 'action': 'read_file', 'args': {'path': '/tmp/clear_notes.txt'}, 'risk': 'low'}]}

for i, plan in enumerate([plan1, plan2, plan3], 1):
    p1 = pass1(plan)
    p2 = pass2(p1, plan)
    sig = p2['intent_signals']
    print(f'Case {i}: implicit_destructive={sig[\"implicit_destructive\"][\"value\"]} explicit_destructive={sig[\"explicit_destructive\"][\"value\"]}')
"
```
Results: clean read → False/False, purge with write scope → True/False, clear in filename on read-only → False/False (scope gate blocked).

#### Task 6 — decision_engine (`iris/decision_engine.py`)
Phase 1 rules only. Verdicts: `proceed | require_confirmation | reject`.
Rule priority order:
1. Unknown action → reject
2. Irreversible action → require_confirmation
3. System config action → require_confirmation
4. Implicit destructive → require_confirmation
5. Explicit destructive → require_confirmation
6. Default → proceed

**Tested:**
```bash
cd /home/nikuhkid/iris && python3 -c "
from iris.plan_analysis_initial import analyze as pass1
from iris.plan_analysis_final import analyze as pass2
from iris.decision_engine import decide

cases = [
    ('clean read', {'intent': 'read_only', 'steps': [{'id': 'step_1', 'action': 'read_file', 'args': {'path': '/tmp/test.txt'}, 'risk': 'low'}]}),
    ('unknown action', {'intent': 'single_action', 'steps': [{'id': 'step_1', 'action': 'launch_missiles', 'args': {}, 'risk': 'high'}]}),
    ('irreversible', {'intent': 'destructive', 'steps': [{'id': 'step_1', 'action': 'delete_file', 'args': {'path': '/tmp/test.txt'}, 'risk': 'high'}]}),
    ('implicit destructive', {'intent': 'single_action', 'steps': [{'id': 'step_1', 'action': 'purge', 'args': {'target': '/tmp/logs'}, 'risk': 'high'}]}),
]

for label, plan in cases:
    p1 = pass1(plan)
    p2 = pass2(p1, plan)
    print(f'{label}:', decide(p2, plan))
"
```
Results: proceed / reject (unknown) / require_confirmation (irreversible) / require_confirmation (irreversible via purge — Rule 2 fires before Rule 4, correct priority).

---

### Files Created This Session
```
iris/
├── Modelfile.slot1
├── Modelfile.slot2
├── CHANGELOG.md
├── schema/
│   └── plan.json
├── prompts/
│   └── planning_system.txt
├── config/
│   └── action_map.json
└── iris/
    ├── validator.py
    ├── plan_analysis_initial.py
    ├── plan_analysis_final.py
    └── decision_engine.py
```

---

#### Task 7 — response_model_stub (`iris/response_model_stub.py`)
Stub only — no model call. Formats verdict into human-readable string.
Named `response_model_stub` explicitly to signal it's temporary.
Interface contract documented in file — real response_model must honour it when DPO model is ready.
Constraints documented: cannot claim execution, enforcement, or decision authority.

**Tested:**
```bash
cd /home/nikuhkid/iris && python3 -c "
from iris.response_model_stub import respond

cases = [
    ({'verdict': 'proceed', 'reason': 'no blocking rules triggered'},
     {'intent': 'read_only', 'steps': [{'action': 'read_file'}, {'action': 'summarize_text'}]}),
    ({'verdict': 'require_confirmation', 'reason': 'irreversible action(s) detected: [delete_file]'},
     {'intent': 'destructive', 'steps': [{'action': 'delete_file'}]}),
    ({'verdict': 'reject', 'reason': 'unknown action type(s): [launch_missiles]'},
     {'intent': 'single_action', 'steps': [{'action': 'launch_missiles'}]}),
]

for verdict, plan in cases:
    print(respond(verdict, plan)['response'])
"
```

### Updated File Tree
```
iris/
├── Modelfile.slot1
├── Modelfile.slot2
├── CHANGELOG.md
├── schema/
│   └── plan.json
├── prompts/
│   └── planning_system.txt
├── config/
│   └── action_map.json
└── iris/
    ├── validator.py
    ├── plan_analysis_initial.py
    ├── plan_analysis_final.py
    ├── decision_engine.py
    └── response_model_stub.py
```

### Housekeeping
- Added `.gitignore` — excludes `__pycache__/`, `*.pyc`, `*.pyo`, `.env`, build artifacts
- Committed and pushed to GitHub: commit `6b4166e`
  - 12 files, 755 insertions
  - Branch: main

#### input_guard_stub (`iris/input_guard_stub.py`)
Stub — pass-through only. Logs input length, returns clean.
Interface contract documented. Real implementation deferred to Phase 2 when real traffic data exists.
Real guard must implement: max_length, encoding validation, AgentShield/MiniClaw injection catalogue, rejection reason codes.

#### Pipeline entry point (`iris/pipeline.py`)
Wires all Phase 1 components in order:
input → input_guard_stub → translation_layer (slot1) → validator → plan_analysis_initial → plan_analysis_final → decision_engine → response_model_stub

No logic in pipeline.py — sequencing only.
Runnable directly: `python3 -m iris.pipeline "your input here"`

**End-to-end tests:**
```bash
# Clean read
cd /home/nikuhkid/iris && python3 -m iris.pipeline "Read the file at /tmp/test.txt and summarize its contents"
# Result: proceed — Plan ready to execute. Intent: read_only. 1 step(s): read_file.

# Delete
cd /home/nikuhkid/iris && python3 -m iris.pipeline "Delete all log files in /tmp/logs"
# Result: reject — unknown action type(s): ['file_system_operation'] — cannot assess safety
# Note: model produced file_system_operation, not in vocabulary. Unknown gate fired before destructive check. Depth confirmed.

# Purge
cd /home/nikuhkid/iris && python3 -m iris.pipeline "Purge the cache directory at /tmp/cache"
# Result: reject — unknown action type(s): ['purge_directory'] — cannot assess safety
# Note: model produced purge_directory. Vocabulary drift surfaced as designed.
```

**Observations logged for Phase 2:**
- Model produced `file_system_operation` and `purge_directory` — not in action_map.json. Do not add yet. Run 50-prompt exit criteria test first, add full batch from real data.
- Unknown action gate firing before destructive intent check is correct — two independent gates both block. Safety net has depth.
- summarize_text variance at temp 0.2 is expected. Relevant in Phase 2 when clustering failure patterns.

### Commit `c7be5b9` → Phase 1 complete
12 files, 755 insertions. All Phase 1 tasks built, tested individually and end-to-end.

---

## Phase 1 Exit Criteria Test — 2026-04-09

### Test Runner (`tests/exit_criteria_p1.py`)
50 prompts across 5 categories: simple reads (10), writes (10), deletes/destructive (10), multi-step (10), ambiguous/edge (10).
Records per prompt: valid_json, valid_schema, intent, unknown_actions, error.
Raw results saved to `tests/exit_criteria_p1_results.json`.

**Run:**
```bash
cd /home/nikuhkid/iris && python3 tests/exit_criteria_p1.py
```

### Results
```
Total prompts:        50
Valid JSON:           50/50 (100.0%)   PASS
Schema conformance:   48/50 (96.0%)   PASS
Exit threshold:       90%
```

### Failures (2)
Both `cannot_plan` — correct structured failure response, not schema violations.
- [43] "Save this" — too ambiguous, no actionable args
- [44] "Update everything" — too ambiguous, no actionable args

Model correctly admitted it could not plan rather than hallucinating a plausible plan. Better than a schema violation.

### Vocabulary Drift — 33 unique unknown action strings
Phase 2 day one material. Do not add to action_map.json until Phase 2 classification pass.
```
append_to_file, clean, clean_directory, clean_text, database.prune, delete,
delete_files, edit_configuration, edit_file, file_delete, file_operation,
file_overwrite, file_system.wipe_directory, file_system_operation, flush_store,
get_logs, list_directory, modify_yaml, process_data, purge_directory,
query_database, read_files, remove_file, save_to_file, save_url_to_file,
search_engine_query, search_web, shell_command, store, store_result,
summarize_json, update_database, update_timestamp
```

### Phase 1 Status: COMPLETE ✅
All exit criteria passed. Pipeline proven end-to-end. Moving to Phase 2.

### Commit `1ece3e6` — Phase 1 exit criteria test
3 files, 715 insertions.
- `tests/exit_criteria_p1.py` — test runner
- `tests/exit_criteria_p1_results.json` — raw results
- `CHANGELOG.md` — updated

### Session wrap
Phase 1 complete and closed. Next session starts at Phase 2.
Phase 2 entry point: retry logic, basic logging, vocabulary drift classification from `tests/exit_criteria_p1_results.json` (33 unknown action strings).

### Clarification logged — Phase 2 scope
Two separate concerns, do not conflate:

**`action_map.json`** — maps action strings the model produces to operation types (read/write/delete/unknown).
The 33 unknown action strings from Phase 1 exit criteria feed this. Phase 2 day one: classify and populate.

**`implicit_destructive_terms`** — lexical scan of raw user input phrasing.
Does NOT expand from the 33 unknowns. Only changes if Phase 2 input variation testing surfaces phrasing that should have triggered the gate but didn't. Leave untouched until then.

---

## Session 2 — 2026-04-10

### Phase 2 — Day 1: Vocabulary Classification & Input Variation Testing

#### action_map.json — Phase 1 unknowns classified

33 unknown action strings from Phase 1 exit criteria classified and added.
4 intentionally left unknown (catch-alls, no safe classification possible):
- `file_system_operation`, `file_operation`, `process_data`, `shell_command`

**Decisions logged:**
- `file_overwrite` → write + `IRREVERSIBLE_ACTIONS` (write semantics, previous content unrecoverable)
- `edit_configuration` → write + `SYSTEM_CONFIG_ACTIONS` (targets config files, always confirm)
- `summarize_json`, `list_directory`, `read_files`, `get_logs`, `search_web`, `search_engine_query`, `query_database` → read
- `save_to_file`, `save_url_to_file`, `store_result`, `store`, `append_to_file`, `update_database`, `update_timestamp`, `modify_yaml`, `edit_file` → write
- `remove_file`, `delete_files`, `file_delete`, `delete`, `purge_directory`, `file_system.wipe_directory`, `flush_store`, `database.prune`, `clean_directory`, `clean` → delete

#### decision_engine.py — updated constants
- `file_overwrite` added to `IRREVERSIBLE_ACTIONS`
- `edit_configuration` added to `SYSTEM_CONFIG_ACTIONS`

---

#### Input Variation Testing — 80 prompts across 4 batches

Test runner: `tests/input_variation_p2.py`
Prompt style: Niku register — shorthand, direct, no corporate phrasing.
80 prompts: reads (15), writes (15), destructive (20), multi-step + ambiguous (30).
Results saved per batch: `tests/batch1_reads.json`, `batch2_writes.json`, `batch3_destructive.json`, `batch4_multistep_ambiguous.json`

**Run per batch:**
```bash
cd /home/nikuhkid/iris && python3 tests/input_variation_p2.py
```

**Key findings:**

Schema failures: 0 — model produced valid JSON throughout.
`cannot_plan`: 2 — "nuke that", "torch it" — too ambiguous, no target.

Vocabulary drift surfaced — new unknowns by category:
- Read aliases: `file_read`, `file_dump`, `file_peek`, `cat`, `os.listdir`, `tail`, `file_search`, `summarize_content`
- Write aliases: `file_write`, `file_append`, `save`, `log`, `yaml_edit`, `json_update_key`, `append_text_to_file`, `write_log`, `file_transform`
- Delete aliases: `os.remove`, `clear_cache`, `cache.purge`, `db_wipe`, `remove_directory`, `clean_up`, `zero_out`, `disk_wipe`
- DB variants: `db.query` (read), `db.update` (write)
- Destructive without context: `system_reset`
- Stay unknown: `execute_command`, `shell_command`, `network`, `update_user_field`, `json_parse`, `parse_yaml`

Implicit destructive gate — hits: `kill`, `clear`, `trash`, `blow away`, `clean it up`, `get rid of it`, `blow it away`, `drop the old stuff`

Slips (destructive, gate missed):
- `chuck` → proceeded (model produced known action, term not in scope)
- `zero out` → proceeded (same)
- `tidy /tmp/logs` → proceeded (model didn't produce write/delete action, scope gate didn't fire)

Notable misreads:
- `gut` → `file_read` (twice) — model completely misread intent
- `flush` → `network` — unrelated
- `nuke` → `execute_command` — treated as shell call

**Decisions from results:**

`file_write` — consistent across batches (4+ occurrences). Classified as write + `IRREVERSIBLE_ACTIONS`. Cannot distinguish new file vs overwrite from action string alone — asymmetry favours confirmation.

`chuck`, `zero`, `tidy`, `trim` — added to `implicit_destructive_terms`. Context-dependent but structurally dangerous without sufficient information.

`tidy` still slipping — scope gate doesn't fire if model doesn't produce write/delete action. Model behaviour problem, not gate problem. Deferred to Phase 6 system prompt tuning.

`nuke`, `gut`, `blow away`, `torch` — explicitly destructive, not implicit. No change to implicit_destructive_terms for these.

#### Files updated this session
- `config/action_map.json` — expanded with all classified unknowns from P1 + P2 testing
- `iris/decision_engine.py` — `file_write` added to `IRREVERSIBLE_ACTIONS`
- `iris/plan_analysis_final.py` — `chuck`, `zero`, `tidy`, `trim` added to `IMPLICIT_DESTRUCTIVE_TERMS`
- `tests/input_variation_p2.py` — new test runner (80 prompts, 4 batches)
- `tests/batch1_reads.json` — batch 1 raw results
- `tests/batch2_writes.json` — batch 2 raw results
- `tests/batch3_destructive.json` — batch 3 raw results
- `tests/batch4_multistep_ambiguous.json` — batch 4 raw results

#### Phase 2 status — IN PROGRESS
Remaining: retry logic (max 2 on schema failure), logging (all inputs + outputs).

#### Retry logic — `iris/pipeline.py`
`_call_with_retry` wraps the planning model call.
- Retries max 2 times on `invalid_json` or `schema_violation` — technical failures, different sample may succeed
- `cannot_plan` exits immediately — ambiguous input needs clarification from user, not another attempt
- Retry attempt number logged to stdout

Smoke tested: clean read proceeds on attempt 1, `cannot_plan` exits immediately with clarification message.

Commit `aa4a7d5`

#### Phase 2 status — IN PROGRESS
Remaining: logging (all inputs + outputs).

#### Logging — `iris/logger.py` + `iris/pipeline.py`

SQLite logger. Append-only. One row per pipeline run. DB at `iris.db` in project root (gitignored).

Schema designed with Phase 5 in mind — hash chain fields present from day one:
- `previous_hash`, `entry_hash` — SHA-256 fields, null until Phase 5 populates them
- `sequence_gap` — marks rows logged during observer degraded mode (Phase 5)
- No ALTER TABLE needed in Phase 5 — columns are already there

`iris/logger.py` — public API:
- `init_db()` — CREATE TABLE IF NOT EXISTS, idempotent
- `start_run(source, user_id)` — inserts skeleton row, returns UUID run_id
- `update_run(run_id, **fields)` — updates any subset of columns. Unknown columns raise ValueError — loud, not silent. JSON fields (plan_json, analysis_initial, analysis_final) serialized automatically.

`iris/pipeline.py` — logging wired at every stage via `_log()` wrapper (fire-and-forget — logger failure never takes down a run). `run_id` now returned in result dict.

Fields logged per run: raw_input, guarded_input, guard_passed, slot_used, attempts, raw_model_output, valid_json, valid_schema, plan_json, analysis_initial, analysis_final, verdict, verdict_reason, pipeline_error, response.

Commits: `38a8683`

---

#### Smoke test — `tests/smoke_test.py`

Full-stack sanity check. Run at session start to confirm all components intact before touching anything.

57 assertions across 6 sections:
- validator — all 4 return cases
- plan_analysis_initial — operation flags per action type
- plan_analysis_final — implicit destructive gate fires and does not fire, explicit_destructive distinction, pass 1 immutability
- decision_engine — all verdicts, rule priority (unknown fires before irreversible)
- response_model_stub — authority language absent from all verdict paths
- logger — row creation, field population, JSON deserialization, Phase 5 nulls, unknown column raises, run_id uniqueness
- pipeline end-to-end (live model) — proceed and cannot_plan paths, DB row confirmed per run

Exits 0 on all pass, non-zero on any failure.

Result: 57/57 ✓

Commit `f27c3ff`

---

### Phase 2 Status: COMPLETE ✅

Exit criteria met:
- Retry on schema failure (max 2) ✓
- Log all inputs + outputs ✓
- Cluster failures (schema vs logic vs ambiguity) ✓
- Expand implicit_destructive lexical set based on misses ✓

Next session starts at Phase 3.
Phase 3 entry point: slot_2 with original_input isolation, comparator (intent + action_types exact match), selective redundancy trigger (write/delete/state_change only), critical_fail escalation to user.

### Clarification logged — Phase 3 design decisions

**Selective redundancy trigger:**
`state_change` in `plan_analysis_initial` is already defined as `write OR delete`. So the trigger condition for slot 2 is simply `state_change: true` — not a separate check for write/delete/state_change. The BRIEFING lists all three but they collapse to one flag. No more granular condition needed.

**Comparator normalization:**
"Normalization" means sorting the action set so order doesn't matter — not semantic normalization. Exact string match on sorted lists. `["read_file", "write_file"]` == `["write_file", "read_file"]`. No fuzzy matching, no synonyms, no semantic similarity.
