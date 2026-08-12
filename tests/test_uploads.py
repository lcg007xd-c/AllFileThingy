from __future__ import annotations

import uuid

from app.uploads import CHUNK_BYTES


def create_job(client, files=None):
    response = client.post(
        "/api/jobs",
        json={"files": files or [{"name": "first.mp4", "size": 6, "type": "video/mp4"}]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, job, file, data, offset=0):
    return client.put(
        f"/api/jobs/{job['id']}/files/{file['id']}/chunks",
        content=data,
        headers={"Content-Type": "application/octet-stream", "Upload-Offset": str(offset)},
    )


def test_routes_require_auth(client):
    assert client.post("/api/jobs", json={"files": []}).status_code == 401
    assert client.get(f"/api/jobs/{uuid.uuid4()}").status_code == 401
    assert client.delete(f"/api/jobs/{uuid.uuid4()}").status_code == 401


def test_chunk_offsets_resume_and_ready(authed_client, settings):
    job = create_job(authed_client)
    file = job["files"][0]
    first = upload(authed_client, job, file, b"abc")
    assert first.status_code == 200 and first.json() == {"offset": 3}
    retry = upload(authed_client, job, file, b"abc", offset=0)
    assert retry.status_code == 409
    assert retry.headers["upload-offset"] == "3"
    assert authed_client.get(f"/api/jobs/{job['id']}/files/{file['id']}/offset").json()["offset"] == 3
    assert upload(authed_client, job, file, b"def", offset=3).json() == {"offset": 6}
    stored = authed_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["state"] == "ready"
    path = settings.data_dir / "jobs" / job["id"] / "inputs" / f"{file['id']}.upload"
    assert path.read_bytes() == b"abcdef"


def test_limits_and_overflow(authed_client, settings):
    too_big = authed_client.post(
        "/api/jobs", json={"files": [{"name": "x.mp4", "size": settings.max_file_bytes + 1, "type": "video/mp4"}]}
    )
    assert too_big.status_code == 413
    job = create_job(authed_client, [{"name": "x.mov", "size": 2, "type": ""}])
    response = upload(authed_client, job, job["files"][0], b"abc")
    assert response.status_code == 413
    assert authed_client.get(f"/api/jobs/{job['id']}/files/{job['files'][0]['id']}/offset").json()["offset"] == 0


def test_chunk_size_limit(authed_client):
    job = create_job(authed_client, [{"name": "x.mp4", "size": CHUNK_BYTES + 1, "type": "video/mp4"}])
    response = upload(authed_client, job, job["files"][0], b"x" * (CHUNK_BYTES + 1))
    assert response.status_code == 413


def test_ordering_and_state_lock(authed_client):
    job = create_job(authed_client, [
        {"name": "a.mp4", "size": 1, "type": "video/mp4"},
        {"name": "b.mp4", "size": 1, "type": "video/mp4"},
    ])
    reversed_ids = [job["files"][1]["id"], job["files"][0]["id"]]
    ordered = authed_client.put(f"/api/jobs/{job['id']}/order", json={"file_ids": reversed_ids})
    assert ordered.status_code == 200
    assert [file["id"] for file in ordered.json()["files"]] == reversed_ids
    duplicate = authed_client.put(f"/api/jobs/{job['id']}/order", json={"file_ids": [reversed_ids[0], reversed_ids[0]]})
    assert duplicate.status_code == 422


def test_display_name_sanitized_and_traversal_cannot_escape(authed_client, settings, tmp_path):
    job = create_job(authed_client, [{"name": "../../outside\x00.mov", "size": 1, "type": "video/quicktime"}])
    file = job["files"][0]
    assert "/" not in file["display_name"] and "\\" not in file["display_name"] and "\x00" not in file["display_name"]
    assert upload(authed_client, job, file, b"x").status_code == 200
    assert not (tmp_path / "outside.mov").exists()
    assert authed_client.get("/api/jobs/../../etc/passwd").status_code in {404, 405}


def test_delete_removes_database_and_disk(authed_client, settings):
    job = create_job(authed_client)
    directory = settings.data_dir / "jobs" / job["id"]
    assert directory.exists()
    assert authed_client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not directory.exists()
    assert authed_client.get(f"/api/jobs/{job['id']}").status_code == 404

