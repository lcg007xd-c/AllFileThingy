from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    app_password: str
    app_secret: str
    data_dir: Path
    cookie_max_age: int = 7 * 24 * 60 * 60
    max_file_bytes: int = 5 * 1024**3
    max_job_bytes: int = 10 * 1024**3
    min_free_disk_bytes: int = 1024**3
    output_retention_hours: int = 24
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    cleanup_interval_seconds: int = 60 * 60

    @classmethod
    def from_env(cls) -> "Settings":
        password = os.getenv("APP_PASSWORD")
        secret = os.getenv("APP_SECRET")
        if not password:
            raise RuntimeError("APP_PASSWORD is required")
        if not secret:
            raise RuntimeError("APP_SECRET is required")
        if len(secret) < 32:
            raise RuntimeError("APP_SECRET must contain at least 32 characters")
        return cls(
            app_password=password,
            app_secret=secret,
            data_dir=Path(os.getenv("DATA_DIR", "data")).resolve(),
            cookie_max_age=_positive_int("COOKIE_MAX_AGE_SECONDS", 7 * 24 * 60 * 60),
            max_file_bytes=_positive_int("MAX_FILE_BYTES", 5 * 1024**3),
            max_job_bytes=_positive_int("MAX_JOB_BYTES", 10 * 1024**3),
            min_free_disk_bytes=_positive_int("MIN_FREE_DISK_BYTES", 1024**3),
            output_retention_hours=_positive_int("OUTPUT_RETENTION_HOURS", 24),
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=os.getenv("FFPROBE_BIN", "ffprobe"),
            cleanup_interval_seconds=_positive_int("CLEANUP_INTERVAL_SECONDS", 60 * 60),
        )
