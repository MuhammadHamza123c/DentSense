"""Shared Pydantic schemas used across the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    """Base model: rejects unknown fields and trims string whitespace."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class ErrorDetail(BaseModel):
    code: str = Field(examples=["validation_error"])
    message: str = Field(examples=["Some submitted fields are invalid."])
    request_id: str | None = Field(default=None, examples=["9f8c1e2d4b5a4c3e"])
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class CheckResult(BaseModel):
    status: Literal["ok", "degraded", "not_configured", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    service: str
    version: str
    environment: str
    timestamp: datetime
    checks: dict[str, CheckResult]


class RootResponse(BaseModel):
    service: str
    status: Literal["ok"]
    version: str
    environment: str
    api_version: str
    docs_url: str | None
    message: str


class DeleteResponse(BaseModel):
    success: bool = True
    message: str = "Deleted successfully."


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
