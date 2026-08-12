# AllFileThingy

AllFileThingy is a private, self-hosted, mobile-first video stitcher. Select several videos in iPhone Safari, arrange them, upload them in resumable 8 MiB chunks, and download one broadly compatible MP4.

It uses FastAPI, SQLite, plain browser JavaScript, and FFmpeg. There is no cloud storage: inputs, job metadata, work files, and outputs stay on the host in `./data` by default.

## Quick start with Docker Desktop

1. Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Copy `.env.example` to `.env`.
3. Set a long private `APP_PASSWORD` and generate an `APP_SECRET` with at least 32 random characters. For example, `python -c "import secrets; print(secrets.token_urlsafe(48))"` prints a suitable secret.
4. Start the app:

   ```sh
   docker compose up --build -d
   ```

5. Open <http://localhost:8000>. Stop it with `docker compose down`.

Compose publishes port 8000 only on `127.0.0.1`; the app is not directly exposed on the LAN. The bind-mounted `./data` directory survives container replacement. The image installs Debian's FFmpeg package and invokes `ffmpeg` and `ffprobe` inside the container, so host FFmpeg is not used by Docker deployments.

## Use it from an iPhone with Cloudflare Tunnel

With the app running, install `cloudflared` and run:

```sh
cloudflared tunnel --url http://localhost:8000
```

Open the random `trycloudflare.com` URL on the iPhone. The URL is public, but every job, upload, processing, and download endpoint remains protected by `APP_PASSWORD`. The login cookie is signed, expires, is HttpOnly and SameSite, and becomes Secure when Cloudflare reports HTTPS through the forwarded scheme.

Each upload request contains at most 8 MiB of `application/octet-stream`, comfortably below Cloudflare request-size limits even when the complete video is much larger. Files upload sequentially; transient requests retry with bounded exponential backoff and resume from the offset accepted by the server.

For an existing named tunnel, create a Cloudflare ingress rule whose service is `http://localhost:8000`, then run that tunnel normally. Keep the tunnel token or credentials in Cloudflare's standard external configuration or secret store. Never add them to `.env`, Compose, or Git. The container accepts forwarded scheme information because the only host-published listener is loopback.

## Local Python startup

Python 3.12 and FFmpeg/ffprobe must be on `PATH`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:APP_PASSWORD = "choose-a-long-password"
$env:APP_SECRET = "replace-with-at-least-32-random-characters"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips "*"
```

On macOS/Linux, activate the virtual environment and export the two variables instead. If `cloudflared` is not used, the localhost session cookie intentionally works without the Secure flag because localhost uses HTTP.

## Processing behavior

Before encoding, every upload is probed and one video frame is decoded. Each clip is auto-rotated by FFmpeg, scaled without cropping, padded to the first clip's canvas, normalized to 30 fps with reset timestamps, and encoded as H.264 (`libx264`, CRF 20, `fast`, `yuv420p`). Audio becomes stereo AAC at 48 kHz; silent audio is inserted when a clip has none. The first post-rotation canvas is capped at 1920×1080 in landscape or 1080×1920 in portrait and is always even-sized with square pixels.

HLG and PQ clips use FFmpeg's `zscale` and `tonemap` filters to produce SDR BT.709. The job fails with a readable retryable error if the installed FFmpeg lacks those filters. Only one job encodes at a time. Queued jobs resume after restart; a job interrupted while encoding becomes failed and can be started again.

## Configuration

All settings are environment variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `APP_PASSWORD` | required | Password for the private web UI |
| `APP_SECRET` | required | Cookie-signing secret, at least 32 characters |
| `DATA_DIR` | `data` (`/data` in Docker) | SQLite and job media location |
| `COOKIE_MAX_AGE_SECONDS` | `604800` | Login lifetime |
| `MAX_FILE_BYTES` | `5368709120` | Maximum declared bytes per clip |
| `MAX_JOB_BYTES` | `10737418240` | Maximum declared bytes across a job |
| `MIN_FREE_DISK_BYTES` | `1073741824` | Disk reserve enforced at job creation and upload |
| `OUTPUT_RETENTION_HOURS` | `24` | Completed output retention |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Expiration scan interval |
| `FFMPEG_BIN`, `FFPROBE_BIN` | command names | Optional executable overrides |

Completed outputs expire after 24 hours by default. Safe cleanup runs on startup and periodically, removing only expired terminal jobs whose IDs are valid UUIDs. The Delete/start-over action removes a non-processing job immediately. Monitor disk use under `DATA_DIR`; normalized intermediates temporarily require additional space while a job processes.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker build -t allfilethingy:v1 .
docker run --rm -e APP_PASSWORD=test -e APP_SECRET=a-test-secret-that-is-longer-than-32-characters -e PYTHONPATH=/app -v "${PWD}/tests:/app/tests:ro" allfilethingy:v1 python -m pytest -q /app/tests
```

The FFmpeg integration test generates temporary clips with mixed dimensions and frame rates, a portrait clip, and missing audio. Generated media stays in pytest's temporary directory and is never committed.

## Troubleshooting

- **Login succeeds but immediately returns to login through a tunnel:** run Uvicorn with proxy headers as shown above. The Docker image already has the required flags.
- **Job fails during inspection:** the file is incomplete, corrupt, mislabeled, or has no decodable video stream. Delete it and reselect the source.
- **HDR filter error:** use the Docker image or install an FFmpeg build that includes `zscale` and `tonemap`.
- **Not enough free disk space:** free space in `DATA_DIR`, reduce the job size, or carefully adjust `MIN_FREE_DISK_BYTES`.
- **Processing was interrupted:** start the failed job again. Uploaded clips remain available until explicitly deleted or expired.
- **Tunnel gives a connection error:** confirm <http://localhost:8000/healthz> works on the host before starting `cloudflared`.

## Security notes

Do not commit `.env`, tunnel credentials, or anything under `data`; all are ignored. User filenames are display-only and sanitized. Every stored path is derived from server-generated UUIDs, and FFmpeg is always called with argument arrays—never a shell command or interpolated command string. Use a unique, strong password even for a temporary random tunnel URL.
