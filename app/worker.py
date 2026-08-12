from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from .config import Settings
from .db import Database
from .processor import MediaProcessor, safe_processing_error
from .uploads import now_iso


class JobWorker:
    def __init__(self, db: Database, processor: MediaProcessor, settings: Settings):
        self.db = db
        self.processor = processor
        self.settings = settings
        self.wake = asyncio.Event()
        self.stopping = asyncio.Event()
        self.task: asyncio.Task | None = None

    def recover(self) -> None:
        expires = datetime.now(UTC) + timedelta(hours=self.settings.output_retention_hours)
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE jobs SET state='failed',phase='Interrupted',progress=0,
                   error='Processing was interrupted by a restart. Start the job again to retry.',
                   expires_at=?,updated_at=?
                   WHERE state='processing'""",
                (expires.isoformat(), now_iso()),
            )

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="allfilethingy-worker")

    async def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def notify(self) -> None:
        self.wake.set()

    def _claim(self) -> str | None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            changed = conn.execute(
                """UPDATE jobs SET state='processing',phase='Starting',progress=0,error=NULL,updated_at=?
                   WHERE id=? AND state='queued'""",
                (now_iso(), row["id"]),
            ).rowcount
            return row["id"] if changed else None

    def _progress(self, job_id: str, phase: str, progress: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET phase=?,progress=?,updated_at=? WHERE id=? AND state='processing'",
                (phase, max(0, min(100, progress)), now_iso(), job_id),
            )

    def _process(self, job_id: str) -> None:
        last_value = -1

        def progress(phase: str, value: float) -> None:
            nonlocal last_value
            integer = int(value)
            if integer != last_value or value >= 100:
                last_value = integer
                self._progress(job_id, phase, value)

        try:
            output = self.processor.process(job_id, progress)
            expires = datetime.now(UTC) + timedelta(hours=self.settings.output_retention_hours)
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE jobs SET state='completed',phase='Completed',progress=100,error=NULL,
                       output_size=?,expires_at=?,updated_at=? WHERE id=?""",
                    (output.stat().st_size, expires.isoformat(), now_iso(), job_id),
                )
        except Exception as exc:
            expires = datetime.now(UTC) + timedelta(hours=self.settings.output_retention_hours)
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE jobs SET state='failed',phase='Failed',error=?,expires_at=?,updated_at=?
                       WHERE id=?""",
                    (safe_processing_error(exc), expires.isoformat(), now_iso(), job_id),
                )

    async def run(self) -> None:
        while not self.stopping.is_set():
            job_id = self._claim()
            if job_id:
                await asyncio.to_thread(self._process, job_id)
                continue
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
