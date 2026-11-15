"""Damage detection tests: URL inference, error mapping and endpoint contract.

Roboflow and Supabase are never called. A fake client returns canned payloads and
route tests override storage, the detector and auth dependencies.
"""

from __future__ import annotations

import asyncio
import copy
import io
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.dependencies import (
    get_current_user,
    get_damage_detection_service,
    get_damage_report_repository,
    get_storage_client,
)
from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.main import create_app
from app.schemas.auth import AuthUser
from app.services.damage_detection import DamageDetectionService

USER_ID = uuid4()
IMAGE_URL = "https://example.supabase.co/storage/v1/object/public/damage-images/a.png"

PREDICTION = {
    "x": 100.0,
    "y": 80.0,
    "width": 40.0,
    "height": 30.0,
    "confidence": 0.91,
    "class": "dent",
    "class_id": 0,
    "detection_id": "abc-123",
}
MODEL_RESULT = {
    "inference_id": "inf-1",
    "time": 0.045,
    "image": {"width": 160, "height": 120},
    "predictions": [PREDICTION],
}
# (40 * 30) / (160 * 120) * 100 = 6.25
EXPECTED_PREDICTION = {**PREDICTION, "damage_area_percentage": 6.25}
EXPECTED_RESULT = {**MODEL_RESULT, "predictions": [EXPECTED_PREDICTION]}


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "test",
        "log_level": "WARNING",
        "roboflow_api_key": "rf_test_key_1234567890",
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


class _FakeClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[object, str | None]] = []

    async def infer_async(self, image, model_id=None):  # noqa: ANN001
        self.calls.append((image, model_id))
        if self.error is not None:
            raise self.error
        return self.payload


def _service(client: _FakeClient, **overrides) -> DamageDetectionService:
    return DamageDetectionService(_settings(**overrides), client_factory=lambda: client)


async def test_adds_damage_area_percentage() -> None:
    client = _FakeClient(copy.deepcopy(MODEL_RESULT))

    result = await _service(client).detect(IMAGE_URL)

    assert result == EXPECTED_RESULT
    assert result["predictions"][0]["damage_area_percentage"] == 6.25
    assert client.calls == [(IMAGE_URL, "car-damage-wpmh2/1")]


async def test_unwraps_single_element_list() -> None:
    result = await _service(
        _FakeClient([copy.deepcopy(MODEL_RESULT)])
    ).detect(IMAGE_URL)

    assert result == EXPECTED_RESULT


async def test_missing_image_metadata_leaves_predictions_untouched() -> None:
    payload = {"predictions": [copy.deepcopy(PREDICTION)]}

    result = await _service(_FakeClient(payload)).detect(IMAGE_URL)

    assert result["predictions"] == [PREDICTION]


async def test_unexpected_payload_maps_to_502() -> None:
    with pytest.raises(ExternalServiceError):
        await _service(_FakeClient("not-a-mapping")).detect(IMAGE_URL)


async def test_missing_roboflow_key_is_configuration_error() -> None:
    service = DamageDetectionService(
        _settings(roboflow_api_key=None), client_factory=lambda: _FakeClient({})
    )

    with pytest.raises(ConfigurationError):
        await service.detect(IMAGE_URL)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ExternalServiceError),
        (404, ExternalServiceError),
        (429, ServiceUnavailableError),
        (400, ValidationAppError),
        (500, ServiceUnavailableError),
    ],
)
async def test_roboflow_status_codes_map_to_safe_errors(
    status: int, expected: type[Exception]
) -> None:
    error = RuntimeError("upstream failure")
    error.status_code = status  # type: ignore[attr-defined]

    with pytest.raises(expected):
        await _service(_FakeClient(error=error)).detect(IMAGE_URL)


async def test_timeout_maps_to_503() -> None:
    client = _FakeClient()
    service = _service(client, roboflow_timeout_seconds=0.01)

    async def slow(image, model_id=None):  # noqa: ANN001
        await asyncio.sleep(0.5)

    client.infer_async = slow  # type: ignore[assignment]

    with pytest.raises(ServiceUnavailableError):
        await service.detect(IMAGE_URL)


# -- Endpoint contract ------------------------------------------------------


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []

    async def upload(self, *, path: str, data: bytes, content_type: str) -> str:
        self.uploads.append((path, data, content_type))
        return (
            "https://example.supabase.co/storage/v1/object/public/"
            f"damage-images/{path}"
        )


class _FakeDetector:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def detect(self, image_url: str) -> dict:
        self.urls.append(image_url)
        return copy.deepcopy(EXPECTED_RESULT)


class _FakeReports:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.reports: list[dict] = []
        self.detections: list[tuple[object, object, list[dict]]] = []

    async def create_report(self, **kwargs) -> dict:
        if self.error is not None:
            raise self.error
        row = {
            "id": str(uuid4()),
            "user_id": str(kwargs.get("user_id")),
            "image_url": kwargs["image_url"],
            "name": kwargs.get("name"),
            "lat": kwargs.get("lat"),
            "lng": kwargs.get("lng"),
            "created_at": "2026-11-13T00:00:00+00:00",
            "updated_at": "2026-11-13T00:00:00+00:00",
        }
        self.reports.append(row)
        return row

    async def create_detections(self, *, user_id, report_id, detections) -> list[dict]:
        if self.error is not None:
            raise self.error
        self.detections.append((user_id, report_id, list(detections)))
        return list(detections)

    async def get_report(self, *, report_id, user_id) -> dict | None:
        for report in self.reports:
            if str(report["id"]) == str(report_id) and str(report["user_id"]) == str(user_id):
                return report
        return None

    async def get_detections(self, *, report_id, user_id) -> list[dict]:
        out: list[dict] = []
        for uid, rid, rows in self.detections:
            if str(rid) == str(report_id) and str(uid) == str(user_id):
                for row in rows:
                    out.append(
                        {
                            "id": str(uuid4()),
                            "report_id": str(rid),
                            "user_id": str(uid),
                            **row,
                            "created_at": "2026-11-13T00:00:00+00:00",
                        }
                    )
        return out


def _user() -> AuthUser:
    return AuthUser(id=USER_ID, email="driver@example.com", role="authenticated")


def _client(
    settings: Settings,
    *,
    storage: _FakeStorage | None = None,
    detector: _FakeDetector | None = None,
    reports: _FakeReports | None = None,
) -> TestClient:
    app = create_app(settings)
    if storage is not None and detector is not None and reports is not None:
        app.dependency_overrides[get_storage_client] = lambda: storage
        app.dependency_overrides[get_damage_detection_service] = lambda: detector
        app.dependency_overrides[get_damage_report_repository] = lambda: reports
        app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=False)


def _png_upload() -> tuple[str, bytes, str]:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), (1, 2, 3)).save(buffer, format="PNG")
    return ("car.png", buffer.getvalue(), "image/png")


def test_detect_requires_authentication() -> None:
    with _client(_settings()) as client:
        response = client.post(
            "/api/v1/damage/detect", files={"file": _png_upload()}
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_detect_requires_auth_even_without_a_file() -> None:
    with _client(_settings()) as client:
        response = client.post("/api/v1/damage/detect")

    assert response.status_code == 401


def test_detect_uploads_to_storage_then_infers_from_public_url() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()
    reports = _FakeReports()

    with _client(_settings(), storage=storage, detector=detector, reports=reports) as client:
        response = client.post(
            "/api/v1/damage/detect",
            files={"file": _png_upload()},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    body = response.json()

    # Stored under the authenticated user's folder, unique name, same bytes.
    assert len(storage.uploads) == 1
    path, data, content_type = storage.uploads[0]
    assert path.startswith(f"{USER_ID}/")
    assert path.endswith(".png")
    assert content_type == "image/png"
    assert data.startswith(b"\x89PNG\r\n\x1a\n")

    # Roboflow was called with the public URL of that object.
    assert detector.urls == [
        f"https://example.supabase.co/storage/v1/object/public/damage-images/{path}"
    ]
    assert body["image_url"] == detector.urls[0]

    # Report-level fields are echoed back alongside the model result.
    assert body["user_id"] == str(USER_ID)
    assert body["name"] == "car.png"
    assert body["lat"] is None
    assert body["lng"] is None

    # Roboflow's payload is passed through (minus predictions, which are stored).
    assert "predictions" not in body
    assert body["image"] == {"width": 160, "height": 120}
    assert body["time"] == pytest.approx(0.045)
    assert "disclaimer" not in body
    assert "no_damage_message" not in body
    assert "summary" not in body

    # The photo and its detections were persisted for the signed-in user.
    assert body["report_id"] is not None
    assert len(reports.reports) == 1
    assert reports.reports[0]["user_id"] == str(USER_ID)
    assert reports.reports[0]["image_url"] == body["image_url"]
    assert len(reports.detections) == 1
    _, report_id, detection_rows = reports.detections[0]
    assert report_id == UUID(body["report_id"])
    assert detection_rows[0]["damage_class"] == "dent"
    assert detection_rows[0]["x"] == 100.0
    assert detection_rows[0]["y"] == 80.0
    assert detection_rows[0]["width"] == 40.0
    assert detection_rows[0]["height"] == 30.0
    assert detection_rows[0]["damage_area_percentage"] == 6.25


def test_detect_rejects_non_image_before_upload_or_inference() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()

    with _client(_settings(), storage=storage, detector=detector) as client:
        response = client.post(
            "/api/v1/damage/detect",
            files={"file": ("notes.txt", b"hello world, not an image", "text/plain")},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert storage.uploads == []
    assert detector.urls == []


def test_detect_requires_the_file_field() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()

    with _client(_settings(), storage=storage, detector=detector) as client:
        response = client.post(
            "/api/v1/damage/detect", headers={"Authorization": "Bearer test-token"}
        )

    assert response.status_code == 422
    assert storage.uploads == []
    assert detector.urls == []
def test_detect_stores_optional_location_and_label() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()
    reports = _FakeReports()

    with _client(_settings(), storage=storage, detector=detector, reports=reports) as client:
        response = client.post(
            "/api/v1/damage/detect",
            files={"file": _png_upload()},
            data={"name": "My Car", "lat": "33.7", "lng": "73.1"},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert reports.reports[0]["name"] == "My Car"
    assert reports.reports[0]["lat"] == pytest.approx(33.7)
    assert reports.reports[0]["lng"] == pytest.approx(73.1)


def test_detect_returns_results_even_if_persistence_fails() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()
    reports = _FakeReports(error=ExternalServiceError("database down"))

    with _client(_settings(), storage=storage, detector=detector, reports=reports) as client:
        response = client.post(
            "/api/v1/damage/detect",
            files={"file": _png_upload()},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.json()["report_id"] is None
    assert "predictions" not in response.json()
def test_get_stored_report_returns_detections() -> None:
    storage = _FakeStorage()
    detector = _FakeDetector()
    reports = _FakeReports()

    with _client(_settings(), storage=storage, detector=detector, reports=reports) as client:
        created = client.post(
            "/api/v1/damage/detect",
            files={"file": _png_upload()},
            headers={"Authorization": "Bearer test-token"},
        )
        assert created.status_code == 200
        report_id = created.json()["report_id"]

        fetched = client.get(
            f"/api/v1/damage/reports/{report_id}",
            headers={"Authorization": "Bearer test-token"},
        )

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == report_id
    assert body["user_id"] == str(USER_ID)
    assert len(body["detections"]) == 1
    assert body["detections"][0]["damage_class"] == "dent"
    assert body["detections"][0]["damage_area_percentage"] == 6.25


def test_get_unknown_report_returns_404() -> None:
    reports = _FakeReports()
    with _client(_settings(), reports=reports, storage=_FakeStorage(), detector=_FakeDetector()) as client:
        # Need auth override; _client only overrides when all three given (yes).
        response = client.get(
            f"/api/v1/damage/reports/{uuid4()}",
            headers={"Authorization": "Bearer test-token"},
        )
    assert response.status_code == 404
