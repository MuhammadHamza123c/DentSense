"""Authentication request/response schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.common import ApiModel

_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,19}$")


class AuthUser(BaseModel):
    """Safe subset of a Supabase ``auth.users`` record."""

    id: UUID
    email: EmailStr | None = None
    phone: str | None = None
    role: str | None = None
    email_confirmed_at: datetime | None = None
    created_at: datetime | None = None
    last_sign_in_at: datetime | None = None
    user_metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore", "str_strip_whitespace": True}


class AuthSession(BaseModel):
    """Supabase session tokens. Treat as bearer credentials."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    expires_at: int | None = None

    model_config = {"extra": "ignore"}


class SignUpRequest(ApiModel):
    email: EmailStr = Field(examples=["driver@example.com"])
    password: str = Field(min_length=8, max_length=72, examples=["StrongPass123"])
    full_name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, value: str) -> str:
        if not any(ch.isalpha() for ch in value):
            raise ValueError("Password must contain at least one letter.")
        if not any(ch.isdigit() for ch in value):
            raise ValueError("Password must contain at least one digit.")
        return value


class LoginRequest(ApiModel):
    email: EmailStr = Field(examples=["driver@example.com"])
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RefreshRequest(ApiModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class AuthResponse(ApiModel):
    user: AuthUser
    session: AuthSession | None = Field(
        default=None,
        description="Null when Supabase requires email confirmation before issuing a session.",
    )
    message: str


class MessageResponse(BaseModel):
    message: str


class Profile(BaseModel):
    id: UUID
    full_name: str | None = None
    phone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"extra": "ignore", "str_strip_whitespace": True}


class MeResponse(BaseModel):
    user: AuthUser
    profile: Profile | None = None


class UpdateProfileRequest(ApiModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, examples=["+923001234567"])

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        if not _PHONE_RE.match(normalised):
            raise ValueError(
                "Phone must be 7-20 characters, may start with '+', and contain "
                "only digits, spaces, dashes or parentheses."
            )
        return normalised

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> UpdateProfileRequest:
        if not self.model_fields_set:
            raise ValueError("Provide at least one of 'full_name' or 'phone'.")
        return self
