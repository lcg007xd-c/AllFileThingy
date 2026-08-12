from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.cleanup import CleanupService
from app.uploads import now_iso
from test_uploads import create_job


def test_cleanup_only_expired_terminal_jobs(authed_client, settings):
    expired = create_job(authed_client)
    active = create_job(authed_client)
    future = create_job(authed_client)
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with authed_client.app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET state='failed',expires_at=? WHERE id=?", (old, expired["id"]))
        conn.execute("UPDATE jobs SET expires_at=? WHERE id=?", (old, active["id"]))
        conn.execute("UPDATE jobs SET state='completed',expires_at=? WHERE id=?", (later, future["id"]))
    cleanup = CleanupService(authed_client.app.state.db, settings.data_dir / "jobs", 3600)
    assert cleanup.cleanup_expired() == [expired["id"]]
    assert not (settings.data_dir / "jobs" / expired["id"]).exists()
    assert (settings.data_dir / "jobs" / active["id"]).exists()
    assert (settings.data_dir / "jobs" / future["id"]).exists()


def test_cleanup_rejects_non_uuid_paths(client, settings):
    outside = settings.data_dir / "outside"
    outside.mkdir()
    stamp = now_iso()
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with client.app.state.db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,state,created_at,updated_at,expires_at) VALUES(?,?,?,?,?)",
            ("../outside", "failed", stamp, stamp, old),
        )
    cleanup = CleanupService(client.app.state.db, settings.data_dir / "jobs", 3600)
    assert cleanup.cleanup_expired() == []
    assert outside.exists()

