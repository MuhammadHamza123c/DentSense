"""Damage detection endpoints."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.dependencies import (
    CurrentUser,
    DamageDetectionServiceDep,
    DamageReportRepositoryDep,
    SettingsDep,
    StorageDep,
    rate_limit,
)
from app.exceptions import AppError, NotFoundError, ServiceUnavailableError, ValidationAppError
from app.logging_config import get_logger
from app.repositories.damage_repository import DamageReportRepository
from app.schemas.damage import (
    DamageDetectionResponse,
    DamageReportDetail,
    DrawBoxResponse,
    StoredDetection,
)
from app.utils.images import read_and_validate_image

logger = get_logger(__name__)

router = APIRouter(prefix="/damage", tags=["Damage Detection"])


@router.post(
    "/detect",
    response_model=DamageDetectionResponse,
    summary="Detect car damage in an uploaded photo",
    description=(
        "Requires a Supabase bearer token. The validated image is uploaded to "
        "Supabase Storage (`damage-images`), and its public URL is sent to the "
        "hosted Roboflow model `car-damage-wpmh2/1`. The response is Roboflow's "
        "result plus the `image_url` that was used and a "
        "`damage_area_percentage` on every prediction.\n\n"
        "The photo is stored as a `damage_reports` row and every prediction as "
        "a `damage_detections` row (x, y, width, height, class, confidence, "
        "area percentage), linked by `report_id` and owned by the caller. "
        "`report_id` is `null` if persistence failed; detection results are "
        "still returned."
    ),
    responses={
        400: {"description": "Empty or unreadable upload"},
        401: {"description": "Missing or invalid token"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported or corrupt image"},
        422: {"description": "Invalid image dimensions or coordinates"},
        429: {"description": "Rate limited"},
        502: {"description": "Roboflow or storage failure"},
        503: {"description": "Service not configured, timed out or unavailable"},
    },
    dependencies=[
        Depends(rate_limit("damage-detect", "damage_detect_rate_limit_per_minute"))
    ],
)
async def detect_damage(
    # Declared first so an unauthenticated request is rejected before the
    # multipart body is read. Only signed-in users can consume inference credits.
    current_user: CurrentUser,
    file: Annotated[
        UploadFile,
        File(
            description="Car photo (JPG, JPEG, PNG or WEBP).",
            alias="file",
        ),
    ],
    settings: SettingsDep,
    storage: StorageDep,
    service: DamageDetectionServiceDep,
    reports: DamageReportRepositoryDep,
    name: Annotated[
        str | None,
        Form(
            max_length=120,
            description="Optional report label (defaults to the uploaded filename).",
        ),
    ] = None,
    label: Annotated[
        str | None,
        Form(
            max_length=120,
            description="Optional label (company name / model name).",
        ),
    ] = None,
    lat: Annotated[
        float | None,
        Form(ge=-90, le=90, description="Optional latitude; send together with lng."),
    ] = None,
    lng: Annotated[
        float | None,
        Form(
            ge=-180,
            le=180,
            description="Optional longitude; send together with lat.",
        ),
    ] = None,
) -> DamageDetectionResponse:
    if (lat is None) != (lng is None):
        raise ValidationAppError("lat and lng must be sent together.")

    image = await read_and_validate_image(
        file,
        max_bytes=settings.max_upload_size_bytes,
        max_pixels=settings.max_image_pixels,
    )

    # One folder per user, unique object name per upload.
    path = f"{current_user.id}/{uuid4().hex}.{image.extension}"
    image_url = await storage.upload(
        path=path, data=image.data, content_type=image.content_type
    )

    payload = await service.detect(image_url)
    payload.pop("image_url", None)
    payload.pop("report_id", None)

    report_id = await _store_report(
        reports=reports,
        user_id=current_user.id,
        image_url=image_url,
        name=name or file.filename,
        label=label,
        lat=lat,
        lng=lng,
        payload=payload,
    )

    # Predictions are persisted; do not echo the full list in the response.
    detection_count = len(_detection_rows(payload))
    payload.pop("predictions", None)

    return DamageDetectionResponse(
        image_url=image_url,
        user_id=current_user.id,
        report_id=report_id,
        name=name or file.filename,
        label=label,
        lat=lat,
        lng=lng,
        detection_count=detection_count,
        **payload,
    )


@router.get(
    "/reports/{report_id}",
    response_model=DamageReportDetail,
    summary="Get a stored damage report with all detections",
    description=(
        "Requires a Supabase bearer token. Returns the report and every "
        "persisted detection (x, y, width, height, class, confidence, area "
        "percentage). Only the owner can read it."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        404: {"description": "Report not found"},
        503: {"description": "Database not configured"},
    },
)
async def get_damage_report(
    current_user: CurrentUser,
    report_id: UUID,
    reports: DamageReportRepositoryDep,
) -> DamageReportDetail:
    if reports is None:
        raise ServiceUnavailableError("Damage report storage is not configured.")
    report = await reports.get_report(
        report_id=report_id, user_id=current_user.id
    )
    if not report:
        raise NotFoundError("Damage report not found.")
    detections = await reports.get_detections(
        report_id=report_id, user_id=current_user.id
    )
    # Car brand lives on the detections now; surface it from the first row.
    label = next(
        (d.get("label") for d in detections if d.get("label")), None
    )
    return DamageReportDetail(
        id=report["id"],
        user_id=report["user_id"],
        name=report.get("name"),
        label=label,
        lat=report.get("lat"),
        lng=report.get("lng"),
        image_url=report["image_url"],
        created_at=report["created_at"],
        updated_at=report["updated_at"],
        detections=[StoredDetection(**detection) for detection in detections],
    )


# -- Persistence -------------------------------------------------------------


async def _store_report(
    *,
    reports: DamageReportRepository | None,
    user_id: UUID,
    image_url: str,
    name: str | None = None,
    label: str | None = None,
    lat: float | None,
    lng: float | None,
    payload: dict[str, Any],
) -> UUID | None:
    """Store the photo report and its detections; never fail the detection."""
    try:
        if reports is None:
            return None
        report = await reports.create_report(
            user_id=user_id,
            image_url=image_url,
            name=name,
            lat=lat,
            lng=lng,
        )
        if not report or not report.get("id"):
            logger.warning("Damage report insert returned no row; nothing stored")
            return None

        report_id = UUID(str(report["id"]))
        detections = _detection_rows(payload)
        if detections:
            await reports.create_detections(
                user_id=user_id,
                report_id=report_id,
                image_url=image_url,
                label=label,
                detections=detections,
            )
        logger.info(
            "Stored damage report %s with %d detection(s)", report_id, len(detections)
        )
        return report_id
    except AppError as exc:
        logger.warning(
            "Damage report was not stored: %s (%s)", exc.message, exc.code
        )
        return None


def _detection_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map Roboflow predictions to ``damage_detections`` columns."""
    image = payload.get("image")
    image_width = _as_float(image.get("width")) if isinstance(image, dict) else None
    image_height = _as_float(image.get("height")) if isinstance(image, dict) else None

    rows: list[dict[str, Any]] = []
    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        return rows

    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        damage_class = prediction.get("class")
        x = _as_float(prediction.get("x"))
        y = _as_float(prediction.get("y"))
        width = _as_float(prediction.get("width"))
        height = _as_float(prediction.get("height"))
        if (
            not isinstance(damage_class, str)
            or not damage_class.strip()
            or x is None
            or y is None
            or width is None
            or height is None
            or width <= 0
            or height <= 0
        ):
            continue

        area = _as_float(prediction.get("damage_area_percentage"))
        if area is None and image_width and image_height:
            area = (width * height) / (image_width * image_height) * 100.0
        if area is None:
            area = 0.0

        class_id = _as_int(prediction.get("class_id"))
        detection_id = prediction.get("detection_id")
        confidence = _as_float(prediction.get("confidence"))
        if confidence is not None:
            confidence = min(max(confidence, 0.0), 1.0)

        rows.append(
            {
                "damage_class": damage_class.strip()[:64],
                "confidence": confidence,
                "class_id": class_id if class_id is not None and class_id >= 0 else None,
                "detection_id": (
                    str(detection_id)[:128] if detection_id is not None else None
                ),
                "x": max(x, 0.0),
                "y": max(y, 0.0),
                "width": width,
                "height": height,
                "damage_area_percentage": round(min(max(area, 0.0), 100.0), 2),
            }
        )
    return rows


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
