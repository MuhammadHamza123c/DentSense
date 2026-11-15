"""Roboflow-backed car damage detection.

Flow: the caller uploads the photo to Supabase Storage first, then passes the
public image URL here. The hosted model (``car-damage-wpmh2/1``) is called with
that URL and its response is returned with one derived field per prediction:
``damage_area_percentage`` (box area / image area * 100).

Security: the Roboflow API key is read from settings (SecretStr) and never
returned, logged or included in error messages.

Transport: a plain ``httpx`` call that mirrors, byte for byte, the request
``inference-sdk``'s ``InferenceHTTPClient`` used to build for a URL input on
the API v0 route. The SDK was dropped because its transitive dependencies
(OpenCV, SciPy, PyAV, matplotlib, supervision — ~350 MB) were never used by
this service and blew past serverless bundle-size limits.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

_ROBOFLOW_API_URL = "https://serverless.roboflow.com"
# Same retry policy the SDK applied: 3 attempts, 1s constant interval.
_RETRYABLE_STATUSES = {429, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


class _RoboflowHTTPError(Exception):
    """Non-200 reply from the Roboflow legacy inference route."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Roboflow responded with HTTP {status_code}")
        self.status_code = status_code


class _RoboflowConnectionError(Exception):
    """Network-level failure while calling the Roboflow inference route."""


class _ImageFetchError(Exception):
    """The stored photo could not be downloaded for analysis."""


class RoboflowV0Client:
    """Minimal drop-in for ``inference_sdk.InferenceHTTPClient`` (API v0).

    Sends the same request the SDK built for a URL input::

        POST https://serverless.roboflow.com/{project}/{version}
            ?confidence=0&allow_reduced_mask_resolution=False
            &overlap=0.14&disable_active_learning=False
        Authorization: Bearer <api key>
        Content-Type: application/json
        <base64 of the original image bytes>

    The API key travels only in the header — never in the URL.
    """

    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=True
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def infer_async(
        self, image_url: str, model_id: str | None = None
    ) -> Any:
        """Download ``image_url``, run it through the hosted model, return JSON."""
        project, _, version = (model_id or "").partition("/")
        if not project or not version or "/" in version:
            raise ConfigurationError(
                "Damage detection is misconfigured on the server."
            )

        image_bytes = await self._download(image_url)
        body = base64.b64encode(image_bytes).decode("utf-8")
        url = f"{_ROBOFLOW_API_URL}/{project}/{version}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        # The exact query string inference-sdk produced for our configuration
        # (InferenceConfiguration defaults are excluded unless not-None).
        params = {
            "confidence": "0",
            "allow_reduced_mask_resolution": "False",
            "overlap": "0.14",
            "disable_active_learning": "False",
        }

        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self._http.post(
                    url, params=params, headers=headers, content=body
                )
            except httpx.HTTPError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise _RoboflowConnectionError("Roboflow request failed") from exc
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue
            if (
                response.status_code in _RETRYABLE_STATUSES
                and attempt < _MAX_ATTEMPTS - 1
            ):
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue
            if response.status_code != 200:
                raise _RoboflowHTTPError(response.status_code)
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError("Roboflow returned a non-JSON response") from exc

    async def _download(self, image_url: str) -> bytes:
        try:
            response = await self._http.get(image_url)
        except httpx.HTTPError as exc:
            raise _ImageFetchError("photo download failed") from exc
        if response.status_code != 200:
            raise _ImageFetchError(f"photo download HTTP {response.status_code}")
        return response.content


class DamageDetectionService:
    """Runs one public image URL through the hosted Roboflow model."""

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._build_client
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def detect(self, image_url: str) -> dict[str, Any]:
        """Send ``image_url`` to Roboflow and return its raw response payload."""
        if not self._settings.roboflow_configured:
            raise ConfigurationError(
                "Damage detection is not configured on the server (ROBOFLOW_API_KEY)."
            )

        client = await self._get_client()

        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                client.infer_async(
                    image_url, model_id=self._settings.roboflow_model_id
                ),
                timeout=self._settings.roboflow_timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("Roboflow inference timed out")
            raise ServiceUnavailableError(
                "Damage detection timed out. Please try again."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - mapped below, never re-raised raw
            raise _map_roboflow_error(exc) from exc

        logger.info(
            "Roboflow inference completed in %.1f ms",
            (time.perf_counter() - started) * 1000,
        )
        return _with_damage_area(_as_payload(raw))

    async def aclose(self) -> None:
        """Release the shared HTTP client (called on app shutdown)."""
        client, self._client = self._client, None
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()

    # -- Client handling ----------------------------------------------------
    def _build_client(self) -> Any:
        api_key = self._settings.roboflow_api_key
        if api_key is None:
            raise ConfigurationError("Roboflow API key is missing.")
        logger.info(
            "Roboflow client initialised (model=%s)", self._settings.roboflow_model_id
        )
        return RoboflowV0Client(
            api_key=api_key.get_secret_value(),
            timeout_seconds=self._settings.roboflow_timeout_seconds,
        )

    async def _get_client(self) -> Any:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = self._client_factory()
        return self._client


def _as_payload(raw: Any) -> dict[str, Any]:
    """Unwrap a single-image response without altering any field."""
    payload = raw
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        raise ExternalServiceError("Damage detection returned an unexpected response.")
    return payload


def _with_damage_area(payload: dict[str, Any]) -> dict[str, Any]:
    """Add ``damage_area_percentage`` to every prediction.

    Formula: ``(box_width * box_height) / (image_width * image_height) * 100``.
    The value is the damaged box area as a percentage of the whole photo,
    rounded to two decimals and clamped to 0–100. Everything else in the
    Roboflow payload is left untouched.
    """
    image = payload.get("image")
    predictions = payload.get("predictions")
    if not isinstance(image, dict) or not isinstance(predictions, list):
        return payload

    try:
        image_width = float(image["width"])
        image_height = float(image["height"])
    except (KeyError, TypeError, ValueError):
        return payload
    if image_width <= 0 or image_height <= 0:
        return payload

    image_area = image_width * image_height
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        try:
            box_width = float(prediction["width"])
            box_height = float(prediction["height"])
        except (KeyError, TypeError, ValueError):
            continue
        area_percentage = (box_width * box_height) / image_area * 100.0
        prediction["damage_area_percentage"] = round(
            min(max(area_percentage, 0.0), 100.0), 2
        )
    return payload


def _map_roboflow_error(exc: Exception) -> Exception:
    """Translate Roboflow client failures into safe API errors (no key leakage)."""
    if isinstance(exc, ConfigurationError):
        return exc
    if isinstance(exc, _ImageFetchError):
        logger.warning("Photo download for inference failed: %s", exc)
        return ExternalServiceError(
            "The uploaded image could not be fetched for analysis. Please try again."
        )
    if isinstance(exc, _RoboflowConnectionError):
        logger.warning("Roboflow unreachable: %s", exc)
        return ExternalServiceError(
            "The damage detection service is unreachable. Please try again."
        )

    status = getattr(exc, "status_code", None)
    logger.warning(
        "Roboflow inference failed: type=%s status=%s",
        type(exc).__name__,
        status if status is not None else "n/a",
    )

    if status in (401, 403):
        return ExternalServiceError(
            "Damage detection rejected the server credentials. Contact the administrator."
        )
    if status == 404:
        return ExternalServiceError(
            "The configured damage detection model was not found."
        )
    if status == 429:
        return ServiceUnavailableError(
            "The damage detection service is rate-limited. Please try again shortly.",
            headers={"Retry-After": "30"},
        )
    if status in (400, 413, 415, 422):
        return ValidationAppError(
            "The model could not process this image. Try a clearer photo."
        )
    if isinstance(status, int) and status >= 500:
        return ServiceUnavailableError(
            "The damage detection service is temporarily unavailable."
        )
    return ExternalServiceError("Damage detection failed. Please try again.")
