"""Message generation schemas."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MessageMadeRequest(BaseModel):
    """Request to generate a Roman-Urdu damage message."""

    user_id: UUID
    report_id: UUID
    damage_classes: list[str] = Field(min_length=1, max_length=20)
    lat: float | None = None
    lng: float | None = None
    mechanic_name: str | None = Field(default=None, max_length=120)
    car_brand: str | None = Field(default=None, max_length=120)


class MessageMadeResponse(BaseModel):
    """Generated message plus the location used."""

    user_id: UUID
    report_id: UUID
    damage_classes: list[str]
    message: str
    mechanic_name: str | None = None
    car_brand: str | None = None
    lat: float | None = None
    lng: float | None = None
    language: Literal["roman-urdu"] = "roman-urdu"
