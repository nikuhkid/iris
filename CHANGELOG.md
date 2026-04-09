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
