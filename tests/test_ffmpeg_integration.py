from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction

import pytest

from app.processor import MediaProcessor, canvas_for
from test_uploads import create_job, upload


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg integration tools are unavailable",
)


def make_clip(path, size, rate, audio):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration=0.7",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=0.7", "-shortest"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    command.append(str(path))
    subprocess.run(command, check=True, timeout=30)


def test_canvas_honors_rotation_and_caps():
    assert canvas_for({"width": 3840, "height": 2160}) == (1920, 1080)
    assert canvas_for({"width": 1920, "height": 1080, "side_data_list": [{"rotation": 90}]}) == (1080, 1920)


def test_stitches_mixed_clips_with_silence(authed_client, tmp_path):
    first = tmp_path / "landscape.mp4"
    second = tmp_path / "portrait.mp4"
    make_clip(first, "320x240", 24, True)
    make_clip(second, "180x320", 15, False)
    payloads = [first.read_bytes(), second.read_bytes()]
    job = create_job(authed_client, [
        {"name": "landscape.mp4", "size": len(payloads[0]), "type": "video/mp4"},
        {"name": "portrait.mp4", "size": len(payloads[1]), "type": "video/mp4"},
    ])
    for record, payload in zip(job["files"], payloads):
        assert upload(authed_client, job, record, payload).status_code == 200

    processor: MediaProcessor = authed_client.app.state.processor
    output = processor.process(job["id"], lambda _phase, _progress: None)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    probe = json.loads(result.stdout)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (320, 240)
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert Fraction(video["r_frame_rate"]) == 30
    assert audio["codec_name"] == "aac" and audio["sample_rate"] == "48000" and audio["channels"] == 2
    assert float(probe["format"]["duration"]) > 1.2
