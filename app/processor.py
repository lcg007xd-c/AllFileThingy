from __future__ import annotations

import json
import math
import re
import subprocess
from collections import deque
from pathlib import Path
from typing import Callable

from .config import Settings
from .db import Database
from .uploads import now_iso

ProgressCallback = Callable[[str, float], None]
HDR_TRANSFERS = {"smpte2084", "arib-std-b67", "smpte-st-2084"}


class ProcessingError(RuntimeError):
    pass


def _rotation(video: dict) -> int:
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            try:
                return round(float(side_data["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    try:
        return round(float(video.get("tags", {}).get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def canvas_for(video: dict) -> tuple[int, int]:
    width, height = int(video["width"]), int(video["height"])
    if _rotation(video) in {90, 270}:
        width, height = height, width
    maximum = (1080, 1920) if height > width else (1920, 1080)
    factor = min(1.0, maximum[0] / width, maximum[1] / height)
    width = max(2, int(math.floor(width * factor / 2) * 2))
    height = max(2, int(math.floor(height * factor / 2) * 2))
    return width, height


def safe_processing_error(value: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    text = re.sub(r"(?:[A-Za-z]:)?[/\\][^ ]+", "the input file", text)
    return (text[:500] or "Video processing failed")


class MediaProcessor:
    """Focused FFmpeg service; upload/job handling stays independent of media operations."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.root = settings.data_dir / "jobs"
        self._filter_names: set[str] | None = None

    def _filters(self) -> set[str]:
        if self._filter_names is None:
            try:
                result = subprocess.run(
                    [self.settings.ffmpeg_bin, "-hide_banner", "-filters"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProcessingError("FFmpeg is unavailable or could not be started") from exc
            self._filter_names = set(re.findall(r"^\s*[.A-Z|]{3}\s+(\S+)", result.stdout, re.MULTILINE))
        return self._filter_names

    def probe(self, path: Path) -> dict:
        try:
            result = subprocess.run(
                [
                    self.settings.ffprobe_bin,
                    "-v", "error",
                    "-show_streams",
                    "-show_format",
                    "-of", "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProcessingError("ffprobe is unavailable or timed out") from exc
        if result.returncode != 0:
            raise ProcessingError("A selected file is not a readable video")
        try:
            probe = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProcessingError("ffprobe returned invalid media information") from exc
        videos = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"]
        if not videos or not videos[0].get("width") or not videos[0].get("height"):
            raise ProcessingError("A selected file does not contain a video stream")
        return probe

    def _duration(self, probe: dict) -> float:
        candidates = [probe.get("format", {}).get("duration")]
        candidates.extend(stream.get("duration") for stream in probe.get("streams", []))
        for value in candidates:
            try:
                duration = float(value)
                if duration > 0:
                    return duration
            except (TypeError, ValueError):
                continue
        raise ProcessingError("A clip has no usable duration")

    def _run_ffmpeg(
        self,
        arguments: list[str],
        duration: float,
        phase: str,
        base: float,
        span: float,
        callback: ProgressCallback,
    ) -> None:
        command = [
            self.settings.ffmpeg_bin,
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *arguments,
            "-progress", "pipe:1", "-nostats",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise ProcessingError("FFmpeg is unavailable or could not be started") from exc
        recent: deque[str] = deque(maxlen=16)
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            recent.append(line)
            key, _, raw_value = line.partition("=")
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    seconds = int(raw_value) / 1_000_000
                    callback(phase, min(base + span, base + span * seconds / max(duration, 0.01)))
                except ValueError:
                    pass
        return_code = process.wait()
        if return_code != 0:
            detail = next((line for line in reversed(recent) if line and "=" not in line), "")
            raise ProcessingError(detail or f"FFmpeg exited with status {return_code}")
        callback(phase, base + span)

    def process(self, job_id: str, callback: ProgressCallback) -> Path:
        with self.db.connect() as conn:
            job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
            files = conn.execute(
                "SELECT * FROM files WHERE job_id=? ORDER BY position", (job_id,)
            ).fetchall()
        if job is None or not files:
            raise ProcessingError("The job has no uploaded clips")

        job_dir = self.root / job_id
        work_dir = job_dir / "work"
        work_dir.mkdir(exist_ok=True)
        probes: list[dict] = []
        durations: list[float] = []
        callback("Inspecting clips", 1)
        for index, file in enumerate(files):
            input_path = job_dir / "inputs" / f"{file['id']}.upload"
            if not input_path.is_file() or input_path.stat().st_size != file["expected_size"]:
                raise ProcessingError("An uploaded clip is incomplete")
            probe = self.probe(input_path)
            video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
            # Decode one frame, not just container metadata, before starting expensive work.
            check = subprocess.run(
                [self.settings.ffmpeg_bin, "-v", "error", "-nostdin", "-i", str(input_path),
                 "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
                capture_output=True,
                timeout=60,
            )
            if check.returncode != 0:
                raise ProcessingError("A selected file contains an undecodable video stream")
            probes.append(probe)
            durations.append(self._duration(probe))
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE files SET probe_json=?,validated=1 WHERE id=? AND job_id=?",
                    (json.dumps(probe, separators=(",", ":")), file["id"], job_id),
                )
            callback("Inspecting clips", 2 + 6 * (index + 1) / len(files))

        first_video = next(stream for stream in probes[0]["streams"] if stream.get("codec_type") == "video")
        canvas_width, canvas_height = canvas_for(first_video)
        normalized: list[Path] = []
        normalize_span = 82 / len(files)
        for index, (file, probe, duration) in enumerate(zip(files, probes, durations)):
            input_path = job_dir / "inputs" / f"{file['id']}.upload"
            output_path = work_dir / f"normalized-{index:04d}.mp4"
            video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
            has_audio = any(stream.get("codec_type") == "audio" for stream in probe["streams"])
            transfer = str(video.get("color_transfer", "")).lower()
            base_video = (
                f"scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={canvas_width}:{canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,fps=30,setpts=PTS-STARTPTS"
            )
            if transfer in HDR_TRANSFERS:
                missing = {"zscale", "tonemap"} - self._filters()
                if missing:
                    raise ProcessingError(
                        "This HDR clip requires FFmpeg filters that are not installed: " + ", ".join(sorted(missing))
                    )
                video_filter = (
                    "zscale=t=linear:npl=100,format=gbrpf32le,"
                    "tonemap=tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,"
                    f"{base_video},format=yuv420p"
                )
            else:
                video_filter = f"{base_video},format=yuv420p"
            args = ["-i", str(input_path)]
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_input = "0:a:0" if has_audio else "1:a:0"
            args += [
                "-filter_complex",
                f"[0:v:0]{video_filter}[v];"
                f"[{audio_input}]aresample=48000:async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                "apad,asetpts=PTS-STARTPTS[a]",
                "-map", "[v]", "-map", "[a]", "-shortest",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart",
                str(output_path),
            ]
            self._run_ffmpeg(
                args, duration, f"Normalizing clip {index + 1} of {len(files)}",
                8 + index * normalize_span, normalize_span, callback,
            )
            normalized.append(output_path)

        concat_path = work_dir / "concat.txt"
        concat_path.write_text(
            "".join(f"file '{path.name}'\n" for path in normalized), encoding="utf-8"
        )
        output_path = job_dir / "output.mp4"
        self._run_ffmpeg(
            ["-f", "concat", "-safe", "1", "-i", str(concat_path),
             "-c", "copy", "-movflags", "+faststart", str(output_path)],
            sum(durations), "Finalizing MP4", 90, 9, callback,
        )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ProcessingError("FFmpeg did not produce an output file")
        callback("Completed", 100)
        return output_path

