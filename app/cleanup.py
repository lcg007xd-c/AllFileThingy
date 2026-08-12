from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .db import Database


class CleanupService:
    def __init__(self, db: Database, jobs_root: Path, interval_seconds: int):
        self.db = db
        self.jobs_root = jobs_root.resolve()
        self.interval_seconds = interval_seconds
        self.task: asyncio.Task | None = None
        self.stopping = asyncio.Event()

    def _safe_directory(self, job_id: str) -> Path | None:
        try:
            normalized = str(uuid.UUID(job_id))
        except (ValueError, AttributeError):
            return None
        candidate = (self.jobs_root / normalized).resolve()
        if candidate.parent != self.jobs_root:
            return None
        return candidate

    def cleanup_expired(self, at: datetime | None = None) -> list[str]:
        cutoff = (at or datetime.now(UTC)).isoformat()
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT id FROM jobs
                   WHERE state IN ('completed','failed')
                   AND expires_at IS NOT NULL AND expires_at <= ?""",
                (cutoff,),
            ).fetchall()
        removed: list[str] = []
        for row in rows:
            job_id = row["id"]
            directory = self._safe_directory(job_id)
            if directory is None:
                continue
            with self.db.connect() as conn:
                deleted = conn.execute(
                    """DELETE FROM jobs WHERE id=? AND state IN ('completed','failed')
                       AND expires_at IS NOT NULL AND expires_at <= ?""",
                    (job_id, cutoff),
                ).rowcount
            if deleted:
                shutil.rmtree(directory, ignore_errors=True)
                removed.append(job_id)
        return removed

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="allfilethingy-cleanup")

    async def stop(self) -> None:
        self.stopping.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        while not self.stopping.is_set():
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self.cleanup_expired)

