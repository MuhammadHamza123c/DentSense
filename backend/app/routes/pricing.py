"""Repair cost estimation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import (
    CurrentUser,
    DamageReportRepositoryDep,
    PricingServiceDep,
    rate_limit,
)
from app.exceptions import NotFoundError, ServiceUnavailableError, ValidationAppError
from app.schemas.pricing import (
    DamagePricingItem,
    PriceEstimateRequest,
    PriceEstimateResponse,
)

router = APIRouter(prefix="/pricing", tags=["Repair Cost Estimation"])


@router.post(
    "/estimate",
    response_model=PriceEstimateResponse,
    summary="Estimate repair cost per damage (PKR)",
    description=(
        "Requires a Supabase bearer token. Provide either `items` (damage class + "
        "area percentage) or `report_id` (a stored damage report whose "
        "detections will be priced). The backend maps the area to a severity "
        "band, asks Groq `openai/gpt-oss-120b` for a price per damage in PKR, "
        "and returns each price plus the total it computed itself."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        404: {"description": "Report or damages not found"},
        422: {"description": "Invalid request body"},
        429: {"description": "Rate limited"},
        502: {"description": "Groq failure or unusable model output"},
        503: {"description": "Pricing or storage not configured, timed out or unavailable"},
    },
    dependencies=[
        Depends(rate_limit("pricing-estimate", "pricing_rate_limit_per_minute"))
    ],
)
async def estimate_repair_cost(
    # Declared first so unauthenticated requests are rejected before pricing.
    _current_user: CurrentUser,
    payload: PriceEstimateRequest,
    service: PricingServiceDep,
    reports: DamageReportRepositoryDep,
) -> PriceEstimateResponse:
    items = payload.items

    if payload.report_id is not None:
        if reports is None:
            raise ServiceUnavailableError("Damage report storage is not configured.")
        detections = await reports.get_detections(
            report_id=payload.report_id, user_id=_current_user.id
        )
        if not detections:
            raise NotFoundError("No damages found for this report.")
        items = [
            DamagePricingItem(
                damage_class=detection["damage_class"],
                damage_area_percentage=detection["damage_area_percentage"],
            )
            for detection in detections
            if (detection.get("damage_area_percentage") or 0) > 0
        ]
        if not items:
            raise ValidationAppError(
                "No priced damages found for this report."
            )

    if not items:
        raise ValidationAppError("No damages to price.")

    return await service.estimate(items)
