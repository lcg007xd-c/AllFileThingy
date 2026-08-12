from app.auth import COOKIE_NAME


def test_private_page_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_and_logout(client):
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/login", json={"password": "test-password"})
    assert response.status_code == 200
    assert COOKIE_NAME in response.cookies
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" not in cookie
    assert client.get("/api/session").json() == {"authenticated": True}
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/session").status_code == 401


def test_https_cookie_is_secure(client):
    response = client.post(
        "https://testserver/api/login", json={"password": "test-password"}
    )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_tampered_cookie_is_rejected(client):
    client.cookies.set(COOKIE_NAME, "not-a-valid-signature")
    assert client.get("/api/session").status_code == 401

