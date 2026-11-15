"""Repair cost estimation schemas (PKR).

The client sends the damage class and how much of the photo is damaged. The
damaged area is a percentage computed from the detection box:

    damage_area_percentage = (box_width * box_height)
                             / (image_width * image_height) * 100

Severity and the total are derived deterministically by the backend; only the
per-damage price comes from the LLM.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

Severity = Literal["minor", "moderate", "severe", "critical"]


class DamagePricingItem(BaseModel):
    """One detected damage that needs a price."""

    damage_class: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("damage_class", "damage_type"),
        description="Damage class returned by the detection model.",
        examples=["front-bumper-dent"],
    )
    damage_area_percentage: float = Field(
        gt=0,
        le=100,
        validation_alias=AliasChoices(
            "damage_area_percentage", "area_percentage", "damage_percentage"
        ),
        description=(
            "Damaged area as a percentage of the photo. Compute it from the "
            "detection box: (box_width * box_height) / (image_width * "
            "image_height) * 100."
        ),
        examples=[8.5],
    )

    @field_validator("damage_class")
    @classmethod
    def _clean_class(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("damage_class must not be blank")
        return cleaned


class PriceEstimateRequest(BaseModel):
    """Damages to price in one request.

    Provide either ``items`` (manual list) or ``report_id`` (a stored damage
    report whose detections will be priced). Exactly one is required.
    """

    items: list[DamagePricingItem] | None = Field(
        default=None,
        description="1-20 damages to price.",
    )
    report_id: UUID | None = Field(
        default=None,
        description="Stored damage report id; its detections will be priced.",
    )

    @model_validator(mode="after")
    def _one_source(self) -> "PriceEstimateRequest":
        if (self.items is None) == (self.report_id is None):
            raise ValueError("Provide exactly one of `items` or `report_id`.")
        if self.items is not None and not (1 <= len(self.items) <= 20):
            raise ValueError("items must contain between 1 and 20 entries.")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "items": [
                        {
                            "damage_class": "front-bumper-dent",
                            "damage_area_percentage": 20.8,
                        }
                    ]
                },
                {"report_id": "3ef8af47-ee2f-4853-86cd-70853ad46a4f"},
            ]
        }
    }


class PricedDamage(BaseModel):
    """One damage with its area-based severity and estimated price."""

    damage_class: str
    damage_area_percentage: float
    severity: Severity = Field(description="Severity band derived from the area.")
    severity_factor: float = Field(
        description="Severity multiplier applied to the class base cost."
    )
    price: int = Field(ge=1, description="Estimated repair cost for this damage (PKR).")


class PriceEstimateResponse(BaseModel):
    """Per-damage prices plus the total computed by the backend."""

    currency: Literal["PKR"] = "PKR"
    provider: Literal["groq"] = "groq"
    model: str
    items: list[PricedDamage]
    total: int = Field(ge=0, description="Sum of every item price (PKR).")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "currency": "PKR",
                    "provider": "groq",
                    "model": "openai/gpt-oss-120b",
                    "items": [
                        {
                            "damage_class": "front-bumper-dent",
                            "damage_area_percentage": 20.8,
                            "severity": "severe",
                            "severity_factor": 1.8,
                            "price": 27000,
                        }
                    ],
                    "total": 27000,
                }
            ]
        }
    }
