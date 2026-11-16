"""M10 T8: the production Host/Origin allow-list is exactly
``127.0.0.1:<port>`` and ``localhost:<port>`` — starlette's default
``testserver`` Host is refused like any other foreign name."""
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.main import create_app
from tests.client import make_client


def _app(tmp_path):
    return create_app(
        Settings(data_dir=tmp_path, db_path=tmp_path / "app.db")
    )


def test_host_origin_exact(tmp_path):
    app = _app(tmp_path)
    c = make_client(app)
    assert c.get("/api/health").status_code == 200

    # The exact allow-list: these two authorities on the app port only.
    for host in ("127.0.0.1:8790", "localhost:8790"):
        assert (
            c.get("/api/health", headers={"Host": host}).status_code == 200
        ), host

    # Everything else → 400 bad_host: the old TestClient default, foreign
    # names, right name + wrong port, no port, non-numeric port, ::1.
    for host in (
        "testserver",
        "evil.example.com",
        "127.0.0.1:9999",
        "localhost.evil.com",
        "127.0.0.1",
        "localhost",
        "127.0.0.1:abc",
        "0.0.0.0:8790",
        "[::1]:8790",
    ):
        r = c.get("/api/health", headers={"Host": host})
        assert r.status_code == 400, host
        assert r.json()["code"] == "bad_host", host

    # A default TestClient (Host: testserver) is refused in production.
    assert TestClient(app).get("/api/health").status_code == 400

    # Mutating verbs keep the Origin rules on top of the Host check.
    good_host = {"Host": "127.0.0.1:8790"}
    body = {"media_id": "0" * 32}
    r = c.post(
        "/api/jobs", json=body,
        headers={**good_host, "Origin": "http://127.0.0.1:8790"},
    )
    assert r.status_code != 403  # same-origin passes the guard
    r = c.post(
        "/api/jobs", json=body,
        headers={**good_host, "Origin": "http://localhost:9999"},
    )
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    r = c.post(
        "/api/jobs", json=body,
        headers={**good_host, "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    # header-less mutating requests (curl/tests) still reach the route
    r = c.post("/api/jobs", json=body, headers=good_host)
    assert r.status_code != 403


def test_non_default_port_honoured(tmp_path):
    """The allow-list follows the configured port, not a hardcoded one."""
    app = create_app(
        Settings(
            data_dir=tmp_path, db_path=tmp_path / "app.db", port=9123
        )
    )
    c = make_client(app)
    assert c.get("/api/health").status_code == 200
    for host in ("127.0.0.1:9123", "localhost:9123"):
        assert (
            c.get("/api/health", headers={"Host": host}).status_code == 200
        )
    for host in ("127.0.0.1:8790", "localhost:8790"):
        assert (
            c.get("/api/health", headers={"Host": host}).status_code == 400
        )
