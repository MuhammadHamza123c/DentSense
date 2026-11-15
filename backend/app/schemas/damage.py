"""Damage detection response schema.

Predictions are NOT returned by ``/damage/detect``: they are persisted to
``public.damage_detections`` and can be fetched with
``GET /api/v1/damage/reports/{report_id}`` or priced with
``POST /api/v1/pricing/estimate`` using ``report_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DamageDetectionResponse(BaseModel):
    """Result of one damage detection (predictions stored server-side)."""

    image_url: str = Field(description="Public Supabase Storage URL sent to Roboflow.")
    user_id: UUID = Field(description="Owner of the report (from the bearer token).")
    report_id: UUID | None = Field(
        default=None,
        description=(
            "Stored damage report id (public.damage_reports). Null when the "
            "detection could not be persisted."
        ),
    )
    name: str | None = Field(
        default=None, description="Report name (filename or the `name` form field)."
    )
    label: str | None = Field(
        default=None, description="Selected car brand / company (e.g. Toyota, Suzuki)."
    )
    lat: float | None = Field(default=None, description="Latitude sent with the upload.")
    lng: float | None = Field(default=None, description="Longitude sent with the upload.")
    time: float | None = Field(default=None, description="Roboflow inference time (s).")
    detection_count: int = Field(
        default=0,
        description=(
            "Number of damage boxes Roboflow returned. 0 means the model found "
            "no damage (predictions: [])."
        ),
    )
    image: dict[str, Any] | None = Field(
        default=None, description="Image metadata returned by Roboflow."
    )

    model_config = {"extra": "allow"}


class StoredDetection(BaseModel):
    """One persisted Roboflow damage box."""

    id: UUID
    report_id: UUID
    user_id: UUID
    damage_class: str
    confidence: float | None = None
    class_id: int | None = None
    detection_id: str | None = None
    x: float
    y: float
    width: float
    height: float
    damage_area_percentage: float
    image_url: str | None = None
    label: str | None = None
    created_at: datetime


class DamageReportDetail(BaseModel):
    """A stored report with all of its detections."""

    id: UUID
    user_id: UUID
    name: str | None = None
    label: str | None = None
    lat: float | None = None
    lng: float | None = None
    image_url: str
    created_at: datetime
    updated_at: datetime
    detections: list[StoredDetection] = Field(default_factory=list)


class DrawBox(BaseModel):
    """One box to draw on the damage photo."""

    damage_class: str
    x: float
    y: float
    width: float
    height: float
    confidence: float | None = None


class DrawBoxResponse(BaseModel):
    """Boxes for a stored report, ready to draw on the image."""

    user_id: UUID
    report_id: UUID
    image_url: str | None = None
    boxes: list[DrawBox] = Field(default_factory=list)
