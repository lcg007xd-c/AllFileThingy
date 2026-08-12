from datetime import UTC, datetime


def test_schema_and_foreign_keys(client, settings):
    db = client.app.state.db
    now = datetime.now(UTC).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,state,created_at,updated_at) VALUES(?,?,?,?)",
            ("job-id", "uploading", now, now),
        )
        row = conn.execute("SELECT operation,state FROM jobs WHERE id=?", ("job-id",)).fetchone()
    assert dict(row) == {"operation": "stitch", "state": "uploading"}

