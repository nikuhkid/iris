"""
tests/exit_criteria_p1.py

Phase 1 exit criteria test — 50 prompts, measure JSON validity and schema conformance.
Records: valid_json, valid_schema, intent, unknown_actions per prompt.
Prints summary at end.
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from iris.validator import validate
from iris.plan_analysis_initial import analyze as pass1

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planning_system.txt"
OLLAMA_URL  = "http://localhost:11434/api/chat"
SLOT1_MODEL = "iris-slot1"

PROMPTS = [
    # Simple reads (10)
    "Read the file at /tmp/notes.txt",
    "List the contents of /home/nikuhkid/documents",
    "What's in /etc/hosts?",
    "Show me the contents of /tmp/config.yaml",
    "Read /var/log/syslog",
    "List all files in /tmp",
    "What files are in /home/nikuhkid?",
    "Read the file /tmp/data.json",
    "Show me what's inside /tmp/output.txt",
    "List the directory /home/nikuhkid/iris",
    # Writes (10)
    "Save the text 'hello world' to /tmp/hello.txt",
    "Update /tmp/config.yaml with the key debug set to true",
    "Store the result to /tmp/output.txt",
    "Write the string 'done' to /tmp/status.txt",
    "Append the log entry 'started' to /tmp/app.log",
    "Save user preferences to /tmp/prefs.json",
    "Update the database entry for user id 42",
    "Store the search results to /tmp/results.txt",
    "Write the summary to /tmp/summary.md",
    "Save the processed data to /tmp/processed.json",
    # Deletes / destructive (10)
    "Remove the file /tmp/old.txt",
    "Clean up the log files in /tmp/logs",
    "Purge all entries in the cache directory",
    "Wipe the contents of /tmp/scratch",
    "Delete /tmp/test.txt",
    "Remove all files older than 7 days in /tmp",
    "Clear the temp directory at /tmp/cache",
    "Prune old database entries from last month",
    "Reset the config file at /tmp/config.yaml to defaults",
    "Flush the session store",
    # Multi-step (10)
    "Search for Python tutorials and save the results to /tmp/results.txt",
    "Read /tmp/data.json and summarize its contents",
    "List the files in /tmp and delete any that end in .log",
    "Read /tmp/input.txt then write the uppercase version to /tmp/output.txt",
    "Search for recent news about AI and store the top result to /tmp/news.txt",
    "Read the config at /tmp/config.yaml and update the debug flag to false",
    "List /tmp, read each .txt file, and summarize all contents",
    "Query the database for user 42 then update their last_seen timestamp",
    "Read /tmp/report.md and save a cleaned version to /tmp/report_clean.md",
    "Search for 'ollama documentation' and save the first result url to /tmp/links.txt",
    # Ambiguous / edge (10)
    "Do the thing with the file",
    "Clean it up",
    "Save this",
    "Update everything",
    "Remove the old stuff",
    "Process the data",
    "Fix the config",
    "Get me the logs",
    "Store it somewhere",
    "Delete that",
]


def _call_model(user_input: str) -> str:
    system_prompt = PROMPT_PATH.read_text()
    payload = {
        "model": SLOT1_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input}
        ]
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    return data["message"]["content"]


def _known_actions() -> set:
    action_map_path = Path(__file__).parent.parent / "config" / "action_map.json"
    with open(action_map_path) as f:
        data = json.load(f)
    known = set()
    for k, v in data.items():
        if not k.startswith("$"):
            known.update(v)
    return known


def _get_unknown_actions(plan: dict) -> list:
    known = _known_actions()
    return [s.get("action") for s in plan.get("steps", []) if s.get("action") not in known]


def run():
    results = []
    vocab_drift = []
    known = _known_actions()

    for i, prompt in enumerate(PROMPTS, 1):
        print(f"[{i:02d}/50] {prompt[:60]}", end=" ... ", flush=True)
        try:
            raw = _call_model(prompt)
            validation = validate(raw)

            valid_json   = validation.get("error") != "invalid_json"
            valid_schema = validation["valid"]
            intent       = validation["plan"]["intent"] if valid_schema else None
            unknown      = _get_unknown_actions(validation["plan"]) if valid_schema else []

            vocab_drift.extend(unknown)

            results.append({
                "prompt": prompt,
                "valid_json": valid_json,
                "valid_schema": valid_schema,
                "intent": intent,
                "unknown_actions": unknown,
                "error": validation.get("error") if not valid_schema else None
            })
            status = "✓" if valid_schema else f"✗ ({validation.get('error')})"
            print(status)

        except Exception as e:
            results.append({
                "prompt": prompt,
                "valid_json": False,
                "valid_schema": False,
                "intent": None,
                "unknown_actions": [],
                "error": str(e)
            })
            print(f"✗ (exception: {e})")

    total        = len(results)
    valid_json_c = sum(1 for r in results if r["valid_json"])
    valid_sch_c  = sum(1 for r in results if r["valid_schema"])
    drift_unique = sorted(set(vocab_drift))

    print(f"\n{'='*60}")
    print(f"PHASE 1 EXIT CRITERIA RESULTS")
    print(f"{'='*60}")
    print(f"Total prompts:        {total}")
    print(f"Valid JSON:           {valid_json_c}/{total} ({valid_json_c/total*100:.1f}%)")
    print(f"Schema conformance:   {valid_sch_c}/{total} ({valid_sch_c/total*100:.1f}%)")
    print(f"Exit threshold:       90%")
    print(f"JSON result:          {'PASS' if valid_json_c/total >= 0.9 else 'FAIL'}")
    print(f"Schema result:        {'PASS' if valid_sch_c/total >= 0.9 else 'FAIL'}")
    print(f"\nVocabulary drift ({len(drift_unique)} unique unknown action strings):")
    for a in drift_unique:
        print(f"  - {a}")
    print(f"\nFailed prompts:")
    for i, r in enumerate(results, 1):
        if not r["valid_schema"]:
            print(f"  [{i:02d}] {r['prompt'][:55]} → {r['error']}")

    out_path = Path(__file__).parent / "exit_criteria_p1_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nRaw results saved to {out_path}")


if __name__ == "__main__":
    run()
