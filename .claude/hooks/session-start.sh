#!/bin/bash
set -euo pipefail

# Only run in Claude Code cloud sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# System tools the app and its integration tests need
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg
fi
if ! command -v uv >/dev/null; then
  python3 -m pip install --quiet uv
fi

# Python 3.12 virtualenv (matches the Dockerfile's python:3.12 base image)
hashes=.venv/.session-start.sha256
if ! sha256sum --check --status "$hashes" 2>/dev/null; then
  rm -f "$hashes"
  uv venv --quiet --allow-existing --python 3.12 .venv
  uv pip install --quiet --python .venv/bin/python -r requirements.txt
  sha256sum .claude/hooks/session-start.sh requirements.txt > "$hashes" || rm -f "$hashes"
fi

# Activate the venv for every command in the session
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/.venv\""
    echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi
