"""Nearby mechanic search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.dependencies import (
    CurrentUser,
    MechanicServiceDep,
    SettingsDep,
    rate_limit,
)
from app.schemas.mechanic import NearbyMechanicsResponse

router = APIRouter(prefix="/mechanics", tags=["Nearby Mechanics"])


@router.get(
    "/nearby",
    response_model=NearbyMechanicsResponse,
    summary="Find nearest car-repair mechanics within 5 km",
    description=(
        "Requires a Supabase bearer token. Returns the five nearest car-repair "
        "shops (OSM Overpass `shop=car_repair` / `craft=car_repair`) sorted by "
        "distance, capped at 5 results within 5 km."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        422: {"description": "Invalid coordinates"},
        429: {"description": "Rate limited"},
        502: {"description": "Overpass failure"},
        503: {"description": "Service unavailable"},
    },
    dependencies=[
        Depends(rate_limit("nearby-mechanics", "mechanic_rate_limit_per_minute"))
    ],
)
async def nearby_mechanics(
    current_user: CurrentUser,
    service: MechanicServiceDep,
    settings: SettingsDep,
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude"),
) -> NearbyMechanicsResponse:
    places = await service.nearby(lat=lat, lng=lng)
    return NearbyMechanicsResponse(
        lat=lat,
        lng=lng,
        radius_m=settings.mechanic_search_radius_m,
        mechanics=places,
    )
