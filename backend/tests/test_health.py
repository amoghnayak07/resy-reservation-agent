from fastapi.testclient import TestClient

from app.main import create_app

app = create_app()
client = TestClient(app)


def test_health_returns_expected_keys() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status", "env", "version"}
    assert body["status"] == "ok"


def test_cors_allows_configured_origin() -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_other_origin() -> None:
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers
