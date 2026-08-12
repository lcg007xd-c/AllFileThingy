from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL DEFAULT 'stitch' CHECK(operation = 'stitch'),
    state TEXT NOT NULL CHECK(state IN ('uploading','ready','queued','processing','completed','failed')),
    phase TEXT NOT NULL DEFAULT 'Uploading',
    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 100),
    error TEXT,
    output_size INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
    uploaded_size INTEGER NOT NULL DEFAULT 0 CHECK(uploaded_size >= 0),
    position INTEGER NOT NULL CHECK(position >= 0),
    probe_json TEXT,
    validated INTEGER NOT NULL DEFAULT 0 CHECK(validated IN (0,1)),
    UNIQUE(job_id, position)
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_files_job ON files(job_id, position);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

