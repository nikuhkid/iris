"""
iris/observer.py

Phase 5 observer service. Seals pipeline runs with SHA-256 hash chaining.
Tracks degraded mode — buffers unsealed run_ids and replays when recovered.

Public API
----------
seal_run(run_id)     -- compute and write previous_hash + entry_hash for a completed run
verify_chain()       -- check full chain integrity, return list of violations
replay_buffer()      -- attempt to seal all buffered run_ids, mark gap rows, return summary

Degraded mode
-------------
If seal_run() fails for any reason:
  - run_id is appended to _buffer
  - _degraded is set to True
  - best-effort update_run(run_id, sequence_gap=1) is attempted
    (succeeds if DB is up but observer logic failed; skipped silently if DB is also down)

replay_buffer() re-attempts sealing in insertion order.
Successfully sealed rows get sequence_gap=1 written (retroactive gap marker).
On full buffer clear, _degraded resets to False.

Hash content
------------
All columns except previous_hash and entry_hash, in PRAGMA table_info index order.
Values stringified: str(v) — None becomes "None", integers become digit strings.
Joined with "|". SHA-256 hex digest.

First row:           previous_hash = "GENESIS"
Prior row unsealed:  previous_hash = "UNSEALED:<prior_run_id>"
Subsequent rows:     previous_hash = entry_hash of row with highest id < current id
"""

import hashlib
import sqlite3
from pathlib import Path

from iris import logger

DB_PATH = Path(__file__).parent.parent / "iris.db"

_HASH_EXCLUDE = {"previous_hash", "entry_hash"}

_buffer: list[str] = []
_degraded: bool = False


# ── internals ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_column_order() -> list[str]:
    """Return column names sorted by PRAGMA table_info cid. Derived at runtime."""
    with _connect() as conn:
        rows = conn.execute("PRAGMA table_info(pipeline_log)").fetchall()
    return [row["name"] for row in sorted(rows, key=lambda r: r["cid"])]


def _compute_entry_hash(row: sqlite3.Row, columns: list[str]) -> str:
    """
    Hash all non-excluded columns in index order.
    None -> "None", integers/strings -> str(v).
    Values joined with "|". SHA-256 hex digest.
    """
    parts = [str(row[col]) for col in columns if col not in _HASH_EXCLUDE]
    content = "|".join(parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_previous_entry_hash(current_id: int) -> str:
    """
    Return the entry_hash of the row with the highest id < current_id.
    Returns "GENESIS" if no prior row exists.
    Returns "UNSEALED:<run_id>" if prior row exists but entry_hash is None.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT run_id, entry_hash FROM pipeline_log WHERE id < ? ORDER BY id DESC LIMIT 1",
            (current_id,)
        ).fetchone()

    if row is None:
        return "GENESIS"
    if row["entry_hash"] is None:
        return f"UNSEALED:{row['run_id']}"
    return row["entry_hash"]


# ── public API ────────────────────────────────────────────────────────────────

def seal_run(run_id: str) -> bool:
    """
    Compute and write previous_hash + entry_hash for a completed run.

    Returns True on success, False on failure.
    On failure: buffers run_id, sets _degraded, best-effort sequence_gap=1.
    """
    global _degraded

    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_log WHERE run_id = ?", (run_id,)
            ).fetchone()

        if row is None:
            raise ValueError(f"run_id not found: {run_id}")

        columns = _get_column_order()
        previous_hash = _get_previous_entry_hash(row["id"])
        entry_hash = _compute_entry_hash(row, columns)

        logger.update_run(run_id, previous_hash=previous_hash, entry_hash=entry_hash)
        return True

    except Exception as e:
        print(f"[observer] seal_run failed for {run_id}: {e}")
        _buffer.append(run_id)
        _degraded = True
        try:
            logger.update_run(run_id, sequence_gap=1)
        except Exception:
            pass  # DB down — replay will correct
        return False


def verify_chain() -> list[dict]:
    """
    Read all rows in id order and verify hash chain integrity.

    Returns a list of violation dicts. Empty list = chain intact.
    Each violation: {"run_id": str, "id": int, "violation_type": str}

    violation_type values:
      "unsealed"      — entry_hash is None
      "hash_mismatch" — stored entry_hash != recomputed entry_hash
      "chain_break"   — previous_hash != expected value from prior row
    """
    violations = []

    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_log ORDER BY id ASC"
        ).fetchall()

    if not rows:
        return violations

    columns = _get_column_order()
    prior_entry_hash: str | None = "GENESIS"
    prior_run_id: str | None = None

    for row in rows:
        run_id = row["run_id"]
        row_id = row["id"]

        if row["entry_hash"] is None:
            violations.append({"run_id": run_id, "id": row_id, "violation_type": "unsealed"})
            prior_entry_hash = None
            prior_run_id = run_id
            continue

        recomputed = _compute_entry_hash(row, columns)
        if row["entry_hash"] != recomputed:
            violations.append({"run_id": run_id, "id": row_id, "violation_type": "hash_mismatch"})

        expected_previous = f"UNSEALED:{prior_run_id}" if prior_entry_hash is None else prior_entry_hash
        if row["previous_hash"] != expected_previous:
            violations.append({"run_id": run_id, "id": row_id, "violation_type": "chain_break"})

        prior_entry_hash = row["entry_hash"]
        prior_run_id = run_id

    return violations


def _seal_without_buffer(run_id: str) -> bool:
    """
    Internal: attempt to seal a run without touching _buffer.
    Used by replay_buffer to avoid double-appending on replay failure.
    """
    global _degraded

    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_log WHERE run_id = ?", (run_id,)
            ).fetchone()

        if row is None:
            raise ValueError(f"run_id not found: {run_id}")

        columns = _get_column_order()
        previous_hash = _get_previous_entry_hash(row["id"])
        entry_hash = _compute_entry_hash(row, columns)

        logger.update_run(run_id, previous_hash=previous_hash, entry_hash=entry_hash)
        return True

    except Exception as e:
        print(f"[observer] replay seal failed for {run_id}: {e}")
        _degraded = True
        try:
            logger.update_run(run_id, sequence_gap=1)
        except Exception:
            pass
        return False


def replay_buffer() -> dict:
    """
    Attempt to seal all buffered run_ids in order.
    Mark successfully sealed rows with sequence_gap=1 (retroactive gap marker).
    Clear successfully sealed entries from buffer.
    Reset _degraded if buffer empties.

    Returns {"sealed": int, "remaining": int}.
    """
    global _buffer, _degraded

    sealed = 0
    still_pending = []

    for run_id in _buffer:
        success = _seal_without_buffer(run_id)
        if success:
            try:
                logger.update_run(run_id, sequence_gap=1)
            except Exception as e:
                print(f"[observer] sequence_gap mark failed for {run_id}: {e}")
            sealed += 1
        else:
            still_pending.append(run_id)

    _buffer = still_pending

    if not _buffer:
        _degraded = False

    return {"sealed": sealed, "remaining": len(_buffer)}
