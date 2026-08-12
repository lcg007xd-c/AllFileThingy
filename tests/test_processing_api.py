from __future__ import annotations

import time

from test_uploads import create_job, upload


def test_start_requires_ready_and_prevents_duplicates(authed_client):
    job = create_job(authed_client)
    assert authed_client.post(f"/api/jobs/{job['id']}/start").status_code == 409
    assert upload(authed_client, job, job["files"][0], b"broken").status_code == 200
    first = authed_client.post(f"/api/jobs/{job['id']}/start")
    assert first.status_code == 202
    second = authed_client.post(f"/api/jobs/{job['id']}/start")
    assert second.status_code == 409


def test_invalid_media_fails_with_safe_error(authed_client):
    job = create_job(authed_client)
    upload(authed_client, job, job["files"][0], b"broken")
    authed_client.post(f"/api/jobs/{job['id']}/start")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = authed_client.get(f"/api/jobs/{job['id']}").json()
        if result["state"] == "failed":
            break
        time.sleep(0.05)
    assert result["state"] == "failed"
    assert result["error"]
    assert str(authed_client.app.state.settings.data_dir) not in result["error"]
    assert authed_client.get(f"/api/jobs/{job['id']}/download").status_code == 409


def test_recover_processing_and_keep_queued(client):
    db = client.app.state.db
    from app.uploads import now_iso
    with db.connect() as conn:
        stamp = now_iso()
        conn.execute("INSERT INTO jobs(id,state,created_at,updated_at) VALUES(?,?,?,?)", ("processing-test", "processing", stamp, stamp))
        conn.execute("INSERT INTO jobs(id,state,created_at,updated_at) VALUES(?,?,?,?)", ("queued-test", "queued", stamp, stamp))
    client.app.state.worker.recover()
    with db.connect() as conn:
        interrupted = conn.execute("SELECT state,error FROM jobs WHERE id='processing-test'").fetchone()
        queued = conn.execute("SELECT state FROM jobs WHERE id='queued-test'").fetchone()
    assert interrupted["state"] == "failed" and "restart" in interrupted["error"].lower()
    assert queued["state"] == "queued"

