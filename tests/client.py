"""Shared test client (M10 T8).

The production Host guard accepts exactly ``127.0.0.1:<port>`` and
``localhost:<port>`` — starlette's default ``testserver`` Host is refused.
Every test client goes through ``make_client`` so requests carry an
explicit loopback Host on the app's own port.
"""
from fastapi.testclient import TestClient


def make_client(app, **kwargs) -> TestClient:
    port = app.state.settings.port
    return TestClient(
        app, base_url=f"http://127.0.0.1:{port}", **kwargs
    )
