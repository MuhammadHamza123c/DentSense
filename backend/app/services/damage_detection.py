"""Roboflow-backed car damage detection.

Flow: the caller uploads the photo to Supabase Storage first, then passes the
public image URL here. The hosted model (``car-damage-wpmh2/1``) is called with
that URL and its response is returned with one derived field per prediction:
``damage_area_percentage`` (box area / image area * 100).

Security: the Roboflow API key is read from settings (SecretStr) and never
returned, logged or included in error messages.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


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

    # -- Client handling ----------------------------------------------------
    def _build_client(self) -> Any:
        from inference_sdk import InferenceConfiguration, InferenceHTTPClient

        api_key = self._settings.roboflow_api_key
        if api_key is None:
            raise ConfigurationError("Roboflow API key is missing.")
        client = InferenceHTTPClient(
            api_url="https://serverless.roboflow.com",
            api_key=api_key.get_secret_value(),
        ).configure(
            InferenceConfiguration(
                api_key_transport="header",
                confidence_threshold=0,
                iou_threshold=0.14,
            )
        )
        logger.info(
            "Roboflow client initialised (model=%s)", self._settings.roboflow_model_id
        )
        return client

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
    """Translate SDK/Roboflow failures into safe API errors (no key leakage)."""
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

    name = type(exc).__name__
    if name in {"HTTPServerConnectionError", "RetryError"}:
        return ExternalServiceError(
            "The damage detection service is unreachable. Please try again."
        )
    if name in {"APIKeyNotProvided", "InvalidModelIdentifier"}:
        return ConfigurationError("Damage detection is misconfigured on the server.")
    if name in {"InvalidInputFormatError", "InvalidURLImageInput", "EncodingError"}:
        return ValidationAppError(
            "The model could not process this image. Try a clearer photo."
        )
    return ExternalServiceError("Damage detection failed. Please try again.")
