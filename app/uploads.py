from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import HTTPException, status

from .config import Settings
from .db import Database

CHUNK_BYTES = 8 * 1024 * 1024
MAX_FILES_PER_JOB = 100
DISPLAY_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
ALLOWED_PICKER_EXTENSIONS = {".mov", ".m4v"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean_display_name(value: str) -> str:
    value = DISPLAY_CONTROL.sub(" ", value).replace("/", "_").replace("\\", "_")
    value = " ".join(value.split()).strip(" .")
    return value[:180] or "video"


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Job or file not found") from exc


class UploadService:
    """Disk-backed, ordered resumable uploads shared by media operations."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.root = settings.data_dir / "jobs"

    def _job_dir(self, stored_job_id: str) -> Path:
        return self.root / stored_job_id

    def _input_path(self, stored_job_id: str, stored_file_id: str) -> Path:
        return self._job_dir(stored_job_id) / "inputs" / f"{stored_file_id}.upload"

    def create_job(self, raw_files: Any) -> dict[str, Any]:
        if not isinstance(raw_files, list) or not raw_files:
            raise HTTPException(status_code=422, detail="Choose at least one video")
        if len(raw_files) > MAX_FILES_PER_JOB:
            raise HTTPException(status_code=422, detail=f"No more than {MAX_FILES_PER_JOB} files")

        files: list[dict[str, Any]] = []
        total = 0
        for item in raw_files:
            if not isinstance(item, dict):
                raise HTTPException(status_code=422, detail="Invalid file description")
            name = item.get("name")
            size = item.get("size")
            media_type = item.get("type") or "application/octet-stream"
            if not isinstance(name, str) or not isinstance(size, int) or isinstance(size, bool):
                raise HTTPException(status_code=422, detail="Each file needs a name and size")
            if size <= 0:
                raise HTTPException(status_code=422, detail="Empty files are not accepted")
            if size > self.settings.max_file_bytes:
                raise HTTPException(status_code=413, detail="A file exceeds the configured limit")
            if not isinstance(media_type, str):
                raise HTTPException(status_code=422, detail="Invalid media type")
            suffix = Path(name).suffix.lower()
            if not media_type.lower().startswith("video/") and suffix not in ALLOWED_PICKER_EXTENSIONS:
                raise HTTPException(status_code=415, detail="Only video files are accepted")
            total += size
            if total > self.settings.max_job_bytes:
                raise HTTPException(status_code=413, detail="The job exceeds the configured limit")
            files.append({
                "id": str(uuid.uuid4()),
                "display_name": clean_display_name(name),
                "media_type": media_type[:120],
                "expected_size": size,
            })

        usage = shutil.disk_usage(self.settings.data_dir)
        if usage.free - total < self.settings.min_free_disk_bytes:
            raise HTTPException(status_code=507, detail="Not enough free disk space")

        job_id = str(uuid.uuid4())
        job_dir = self._job_dir(job_id)
        (job_dir / "inputs").mkdir(parents=True, exist_ok=False)
        (job_dir / "work").mkdir()
        created = now_iso()
        try:
            with self.db.connect() as conn:
                conn.execute(
                    "INSERT INTO jobs(id,state,phase,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (job_id, "uploading", "Waiting for uploads", created, created),
                )
                conn.executemany(
                    """INSERT INTO files
                       (id,job_id,display_name,media_type,expected_size,position)
                       VALUES(?,?,?,?,?,?)""",
                    [
                        (file["id"], job_id, file["display_name"], file["media_type"], file["expected_size"], position)
                        for position, file in enumerate(files)
                    ],
                )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return self.get_job(job_id)

    def get_job(self, raw_job_id: str) -> dict[str, Any]:
        job_id = _uuid(raw_job_id)
        with self.db.connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            files = conn.execute(
                """SELECT id,display_name,media_type,expected_size,uploaded_size,position,validated
                   FROM files WHERE job_id=? ORDER BY position""",
                (job_id,),
            ).fetchall()
        result = dict(job)
        result["files"] = [dict(file) for file in files]
        return result

    def offset(self, raw_job_id: str, raw_file_id: str) -> dict[str, int]:
        job_id, file_id = _uuid(raw_job_id), _uuid(raw_file_id)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT uploaded_size,expected_size FROM files WHERE id=? AND job_id=?",
                (file_id, job_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="File not found")
        return {"offset": row["uploaded_size"], "size": row["expected_size"]}

    async def append_chunk(
        self,
        raw_job_id: str,
        raw_file_id: str,
        supplied_offset: int,
        stream: AsyncIterator[bytes],
    ) -> dict[str, int]:
        job_id, file_id = _uuid(raw_job_id), _uuid(raw_file_id)
        if supplied_offset < 0:
            raise HTTPException(status_code=400, detail="Upload offset must be non-negative")
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT f.uploaded_size,f.expected_size,j.state
                   FROM files f JOIN jobs j ON j.id=f.job_id
                   WHERE f.id=? AND f.job_id=?""",
                (file_id, job_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="File not found")
        if row["state"] != "uploading":
            raise HTTPException(status_code=409, detail="Job is no longer accepting uploads")
        if supplied_offset != row["uploaded_size"]:
            raise HTTPException(
                status_code=409,
                detail="Upload offset mismatch",
                headers={"Upload-Offset": str(row["uploaded_size"])},
            )

        remaining = row["expected_size"] - supplied_offset
        if remaining <= 0:
            return {"offset": row["uploaded_size"]}
        temp_path = self._job_dir(job_id) / "work" / f"chunk-{file_id}-{uuid.uuid4()}.part"
        received = 0
        try:
            with temp_path.open("xb") as temp:
                async for data in stream:
                    received += len(data)
                    if received > CHUNK_BYTES:
                        raise HTTPException(status_code=413, detail="Chunk exceeds 8 MiB")
                    if received > remaining:
                        raise HTTPException(status_code=413, detail="Chunk exceeds declared file size")
                    temp.write(data)
                temp.flush()
                os.fsync(temp.fileno())
            if received == 0:
                raise HTTPException(status_code=400, detail="Empty chunk")

            with self.db.connect() as conn:
                current = conn.execute(
                    "SELECT uploaded_size FROM files WHERE id=? AND job_id=?",
                    (file_id, job_id),
                ).fetchone()
                if current is None or current["uploaded_size"] != supplied_offset:
                    expected = current["uploaded_size"] if current else 0
                    raise HTTPException(
                        status_code=409,
                        detail="Upload offset changed",
                        headers={"Upload-Offset": str(expected)},
                    )
                destination = self._input_path(job_id, file_id)
                actual = destination.stat().st_size if destination.exists() else 0
                if actual != supplied_offset:
                    raise HTTPException(status_code=500, detail="Stored upload is inconsistent")
                with destination.open("ab") as output, temp_path.open("rb") as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                new_offset = supplied_offset + received
                conn.execute(
                    "UPDATE files SET uploaded_size=? WHERE id=? AND job_id=?",
                    (new_offset, file_id, job_id),
                )
                incomplete = conn.execute(
                    "SELECT COUNT(*) FROM files WHERE job_id=? AND uploaded_size != expected_size",
                    (job_id,),
                ).fetchone()[0]
                if incomplete == 0:
                    conn.execute(
                        "UPDATE jobs SET state='ready',phase='Ready to process',progress=0,updated_at=? WHERE id=?",
                        (now_iso(), job_id),
                    )
                else:
                    conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now_iso(), job_id))
            return {"offset": new_offset}
        finally:
            temp_path.unlink(missing_ok=True)

    def reorder(self, raw_job_id: str, raw_ids: Any) -> dict[str, Any]:
        job_id = _uuid(raw_job_id)
        if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
            raise HTTPException(status_code=422, detail="file_ids must be a list")
        file_ids = [_uuid(value) for value in raw_ids]
        if len(file_ids) != len(set(file_ids)):
            raise HTTPException(status_code=422, detail="File order contains duplicates")
        with self.db.connect() as conn:
            job = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job["state"] not in {"uploading", "ready"}:
                raise HTTPException(status_code=409, detail="File order is locked")
            existing = [row[0] for row in conn.execute("SELECT id FROM files WHERE job_id=?", (job_id,))]
            if set(existing) != set(file_ids) or len(existing) != len(file_ids):
                raise HTTPException(status_code=422, detail="Order must include every file exactly once")
            conn.execute("UPDATE files SET position=position+1000 WHERE job_id=?", (job_id,))
            for position, file_id in enumerate(file_ids):
                conn.execute(
                    "UPDATE files SET position=? WHERE id=? AND job_id=?",
                    (position, file_id, job_id),
                )
            conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now_iso(), job_id))
        return self.get_job(job_id)

    def delete(self, raw_job_id: str) -> None:
        job_id = _uuid(raw_job_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if row["state"] == "processing":
                raise HTTPException(status_code=409, detail="A processing job cannot be deleted")
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

