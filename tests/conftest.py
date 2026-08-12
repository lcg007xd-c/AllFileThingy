from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("APP_SECRET", "a-test-secret-that-is-longer-than-32-characters")

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        app_password="test-password",
        app_secret="a-test-secret-that-is-longer-than-32-characters",
        data_dir=tmp_path / "data",
        min_free_disk_bytes=1,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


@pytest.fixture
def authed_client(client):
    response = client.post("/api/login", json={"password": "test-password"})
    assert response.status_code == 200
    return client
