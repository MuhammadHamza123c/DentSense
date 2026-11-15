"""Damage message generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import (
    CurrentUser,
    DamageReportRepositoryDep,
    MessageServiceDep,
    rate_limit,
)
from app.exceptions import NotFoundError, PermissionDeniedError, ServiceUnavailableError
from app.schemas.message import MessageMadeRequest, MessageMadeResponse


router = APIRouter(prefix="/message_made", tags=["Damage Message"])


@router.post(
    "",
    response_model=MessageMadeResponse,
    summary="Generate a Roman-Urdu damage message",
    description=(
        "Requires a Supabase bearer token. Send user_id, report_id and a "
        "damage class; the backend verifies the report belongs to the caller, "
        "generates a ~150 word Roman-Urdu message describing the damage and "
        "where it is, and returns it with the lat/lng (current location)."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "user_id does not match the authenticated user"},
        404: {"description": "Report not found"},
        422: {"description": "Invalid request body"},
        429: {"description": "Rate limited"},
        502: {"description": "Groq failure"},
        503: {"description": "Message generation or storage not configured"},
    },
    dependencies=[
        Depends(rate_limit("message-made", "message_rate_limit_per_minute"))
    ],
)
async def make_damage_message(
    current_user: CurrentUser,
    payload: MessageMadeRequest,
    reports: DamageReportRepositoryDep,
    service: MessageServiceDep,
) -> MessageMadeResponse:
    if payload.user_id != current_user.id:
        raise PermissionDeniedError("user_id does not match the authenticated user.")

    if reports is None:
        raise ServiceUnavailableError("Damage report storage is not configured.")
    report = await reports.get_report(
        report_id=payload.report_id, user_id=current_user.id
    )
    if not report:
        raise NotFoundError("Damage report not found.")

    lat = payload.lat if payload.lat is not None else report.get("lat")
    lng = payload.lng if payload.lng is not None else report.get("lng")

    # Car brand: trust the request, else fall back to the stored detection label.
    car_brand = (payload.car_brand or "").strip() or None
    if car_brand is None:
        detections = await reports.get_detections(
            report_id=payload.report_id, user_id=current_user.id
        )
        car_brand = next(
            (d.get("label") for d in detections if d.get("label")), None
        )

    message = await service.make_message(
        damage_classes=payload.damage_classes,
        lat=lat,
        lng=lng,
        image_url=report.get("image_url"),
        mechanic_name=payload.mechanic_name,
        car_brand=car_brand,
    )

    return MessageMadeResponse(
        user_id=current_user.id,
        report_id=payload.report_id,
        damage_classes=payload.damage_classes,
        message=message,
        lat=lat,
        lng=lng,
        mechanic_name=payload.mechanic_name,
        car_brand=car_brand,
    )
