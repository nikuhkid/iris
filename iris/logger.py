"""
iris/logger.py

Append-only SQLite logger for the IRIS pipeline.
One row per pipeline run. Phase 5 hash chaining fields are present but null until Phase 5.

DB: /home/nikuhkid/iris/iris.db (project root)

Schema
------
id                  INTEGER PK AUTOINCREMENT
run_id              TEXT UNIQUE        -- UUID per run
timestamp           TEXT               -- ISO 8601 UTC
source              TEXT               -- cli | discord | claude_interface
user_id             TEXT NULL          -- populated when source != cli

raw_input           TEXT               -- verbatim pre-guard input
guarded_input       TEXT NULL          -- post-sanitization (null if guard rejected)
guard_passed        INTEGER            -- 0/1

slot_used           INTEGER NULL       -- 1, 2, or 3
attempts            INTEGER NULL       -- total planning model attempts
raw_model_output    TEXT NULL          -- verbatim last model response
valid_json          INTEGER NULL       -- 0/1
valid_schema        INTEGER NULL       -- 0/1

plan_json           TEXT NULL          -- validated plan as JSON string
analysis_initial    TEXT NULL          -- pass 1 output as JSON string
analysis_final      TEXT NULL          -- pass 2 output as JSON string

verdict             TEXT NULL          -- proceed | require_confirmation | reject
verdict_reason      TEXT NULL

pipeline_error      TEXT NULL          -- error type if pipeline failed mid-run
response            TEXT NULL          -- final response string sent to user

previous_hash       TEXT NULL          -- Phase 5: SHA-256 of previous row's entry_hash
entry_hash          TEXT NULL          -- Phase 5: SHA-256 of this row's content
sequence_gap        INTEGER DEFAULT 0  -- Phase 5: 1 if logged during observer degraded mode

Public API
----------
init_db()                     -- create table if not exists (idempotent)
start_run(source, user_id)    -- insert skeleton row, return run_id
update_run(run_id, **fields)  -- update any subset of fields by run_id
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "iris.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pipeline_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL UNIQUE,
    timestamp         TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    user_id           TEXT,

    raw_input         TEXT,
    guarded_input     TEXT,
    guard_passed      INTEGER,

    slot_used         INTEGER,
    attempts          INTEGER,
    raw_model_output  TEXT,
    valid_json        INTEGER,
    valid_schema      INTEGER,

    plan_json         TEXT,
    analysis_initial  TEXT,
    analysis_final    TEXT,

    verdict           TEXT,
    verdict_reason    TEXT,
    user_action       TEXT,

    pipeline_error    TEXT,
    response          TEXT,

    previous_hash     TEXT,
    entry_hash        TEXT,
    sequence_gap      INTEGER DEFAULT 0
);
"""

# Columns that accept JSON objects — serialized before insert/update
_JSON_FIELDS = {"plan_json", "analysis_initial", "analysis_final"}

# All settable columns (excludes id, run_id, timestamp — set at row creation)
_UPDATABLE_COLUMNS = {
    "source", "user_id",
    "raw_input", "guarded_input", "guard_passed",
    "slot_used", "attempts", "raw_model_output", "valid_json", "valid_schema",
    "plan_json", "analysis_initial", "analysis_final",
    "verdict", "verdict_reason", "user_action",
    "pipeline_error", "response",
    "previous_hash", "entry_hash", "sequence_gap",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _serialize(fields: dict) -> dict:
    """Serialize any JSON-field values to strings."""
    out = {}
    for k, v in fields.items():
        if k in _JSON_FIELDS and v is not None and not isinstance(v, str):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


def init_db() -> None:
    """Create pipeline_log table if it doesn't exist. Idempotent."""
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)


def start_run(source: str = "cli", user_id: str = None) -> str:
    """
    Insert a skeleton row and return the run_id.
    Call this at the top of pipeline.run() before any processing.

    Args:
        source:  'cli' | 'discord' | 'claude_interface'
        user_id: Discord user ID or similar — None for CLI runs

    Returns:
        run_id (UUID string)
    """
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        conn.execute(
            "INSERT INTO pipeline_log (run_id, timestamp, source, user_id) VALUES (?, ?, ?, ?)",
            (run_id, timestamp, source, user_id)
        )

    return run_id


def update_run(run_id: str, **fields) -> None:
    """
    Update any subset of fields on an existing run row.
    Unknown column names raise ValueError — fail loud, not silent.

    JSON-typed fields (plan_json, analysis_initial, analysis_final) accept
    dicts and are serialized automatically.

    Args:
        run_id: UUID string returned by start_run()
        **fields: column=value pairs to update
    """
    unknown = set(fields) - _UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"[logger] unknown column(s): {unknown}")

    if not fields:
        return

    fields = _serialize(fields)

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [run_id]

    with _connect() as conn:
        conn.execute(
            f"UPDATE pipeline_log SET {set_clause} WHERE run_id = ?",
            values
        )
