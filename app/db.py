"""SQLite persistence. A project is replaced atomically inside one
transaction: validation runs first, so a rejected save cannot alter the
previously stored project."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager

DB_PATH = os.environ.get("LIGHTING_DB", "/data/lighting.db")

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cues (
    id            TEXT NOT NULL,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    fixture       TEXT NOT NULL,
    channel       TEXT NOT NULL,
    target        INTEGER NOT NULL CHECK (target BETWEEN 0 AND 255),
    duration      INTEGER NOT NULL CHECK (duration >= 0),
    start_mode    TEXT NOT NULL CHECK (start_mode IN ('absolute','reference')),
    start_at      INTEGER CHECK (start_at IS NULL OR start_at >= 0),
    ref_id        TEXT,
    ref_anchor    TEXT CHECK (ref_anchor IS NULL OR ref_anchor IN ('start','end')),
    ref_offset    INTEGER CHECK (ref_offset IS NULL OR ref_offset >= 0),
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS idx_cues_project ON cues(project_id, position);
"""


def connect() -> sqlite3.Connection:
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM projects ORDER BY created_at, id").fetchall()
    return [dict(r) for r in rows]


def _row_to_cue(row: sqlite3.Row) -> dict:
    if row["start_mode"] == "absolute":
        start = {"mode": "absolute", "at": row["start_at"]}
    else:
        start = {"mode": "reference", "refId": row["ref_id"],
                 "anchor": row["ref_anchor"], "offset": row["ref_offset"]}
    return {"id": row["id"], "fixture": row["fixture"],
            "channel": row["channel"], "target": row["target"],
            "duration": row["duration"], "start": start}


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    prow = conn.execute("SELECT id, name FROM projects WHERE id = ?",
                        (project_id,)).fetchone()
    if prow is None:
        return None
    rows = conn.execute(
        "SELECT * FROM cues WHERE project_id = ? ORDER BY position",
        (project_id,)).fetchall()
    return {"id": prow["id"], "name": prow["name"],
            "cues": [_row_to_cue(r) for r in rows]}


def replace_project(conn: sqlite3.Connection, project_id: str, name: str,
                    cues: list[dict], *, create: bool) -> dict:
    """Atomically create/replace a project's editable payload.

    Caller MUST have validated ``cues`` with engine.compile_project first.
    """
    with transaction(conn):
        exists = conn.execute("SELECT 1 FROM projects WHERE id = ?",
                              (project_id,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE projects SET name = ?, updated_at = datetime('now') "
                "WHERE id = ?", (name, project_id))
        else:
            if not create:
                raise KeyError(project_id)
            conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)",
                         (project_id, name))
        conn.execute("DELETE FROM cues WHERE project_id = ?", (project_id,))
        for pos, c in enumerate(cues):
            st = c["start"]
            conn.execute(
                "INSERT INTO cues (id, project_id, position, fixture, channel,"
                " target, duration, start_mode, start_at, ref_id, ref_anchor,"
                " ref_offset) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["id"], project_id, pos, c["fixture"], c["channel"],
                 c["target"], c["duration"], st["mode"],
                 st.get("at") if st["mode"] == "absolute" else None,
                 st.get("refId") if st["mode"] == "reference" else None,
                 st.get("anchor") if st["mode"] == "reference" else None,
                 st.get("offset") if st["mode"] == "reference" else None))
    saved = get_project(conn, project_id)
    assert saved is not None
    return saved


def new_id() -> str:
    return uuid.uuid4().hex
