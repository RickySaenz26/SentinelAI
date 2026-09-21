from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint_preserves_contract() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "sentinelai-backend",
        "version": "0.1.0",
    }
    assert response.headers["X-Request-ID"]


def test_liveness_endpoint_accepts_request_id() -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"


def test_readiness_endpoint() -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_route_is_not_found() -> None:
    response = client.get("/api/v1/not-found")

    assert response.status_code == 404
