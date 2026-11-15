"""Nearby mechanics schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Mechanic(BaseModel):
    name: str | None = None
    lat: float
    lng: float
    distance_m: float = Field(description="Distance from the requested point")
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    osm_type: str | None = None
    osm_id: int | None = None


class NearbyMechanicsResponse(BaseModel):
    lat: float
    lng: float
    radius_m: int
    mechanics: list[Mechanic] = Field(description="Nearest car-repair places (max 5)")
