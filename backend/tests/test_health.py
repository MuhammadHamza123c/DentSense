"""Health, root and error-envelope tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def test_root_returns_service_status(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "CarDamageAI API"
    assert body["api_version"] == "v1"
    assert body["environment"] == "test"


def test_health_reports_degraded_without_credentials(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["api"]["status"] == "ok"
    assert body["checks"]["roboflow"]["status"] == "not_configured"
    assert body["checks"]["supabase"]["status"] == "not_configured"


def test_health_reports_ok_when_configured(configured_settings: Settings) -> None:
    app = create_app(configured_settings)
    app.dependency_overrides[get_settings] = lambda: configured_settings

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["roboflow"]["status"] == "ok"
    assert body["checks"]["supabase"]["status"] == "ok"


def test_health_never_leaks_secrets(configured_settings: Settings) -> None:
    app = create_app(configured_settings)
    app.dependency_overrides[get_settings] = lambda: configured_settings

    with TestClient(app) as test_client:
        body = test_client.get("/health").text

    assert "rf_test_key_1234567890" not in body
    assert "sb_secret_test_1234567890" not in body


def test_request_id_is_returned(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
    assert float(response.headers["X-Process-Time-ms"]) >= 0


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad id with spaces"
    assert len(response.headers["X-Request-ID"]) == 32


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_oversized_body_is_rejected_before_parsing(client: TestClient) -> None:
    response = client.post(
        "/health",
        content=b"x" * 10,
        headers={"Content-Length": str(5 * 1024 * 1024)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_validation_errors_use_error_envelope(test_settings: Settings) -> None:
    """A temporary route proves the shared 422 handler shape."""
    from pydantic import BaseModel

    class Payload(BaseModel):
        count: int

    app = create_app(test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings

    @app.post("/_test/validate")
    async def _validate(payload: Payload) -> dict[str, int]:  # pragma: no cover
        return {"count": payload.count}

    with TestClient(app) as test_client:
        response = test_client.post("/_test/validate", json={"count": "not-an-int"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["field"] == "body.count"
