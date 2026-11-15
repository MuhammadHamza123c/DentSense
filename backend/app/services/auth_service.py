"""Authentication business logic.

Trust boundary: every user id used here comes from a Supabase access token that
GoTrue has verified. Ids supplied in request bodies are never trusted.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.logging_config import get_logger
from app.repositories.profile_repository import ProfileRepository
from app.schemas.auth import (
    AuthResponse,
    AuthSession,
    AuthUser,
    MeResponse,
    Profile,
    SignUpRequest,
    UpdateProfileRequest,
)
from app.services.supabase_auth import SupabaseAuthClient

logger = get_logger(__name__)


def extract_session(payload: dict[str, Any]) -> AuthSession | None:
    """Pull a session out of a GoTrue signup/token payload."""
    candidate: Any = payload.get("session")
    if not isinstance(candidate, dict):
        candidate = payload
    if not isinstance(candidate, dict):
        return None
    access_token = candidate.get("access_token")
    refresh_token = candidate.get("refresh_token")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        return None
    expires_at = candidate.get("expires_at")
    try:
        expires_at_int = int(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        expires_at_int = None
    return AuthSession(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=str(candidate.get("token_type") or "bearer"),
        expires_in=_safe_int(candidate.get("expires_in")),
        expires_at=expires_at_int,
    )


def extract_user(payload: dict[str, Any]) -> AuthUser | None:
    """Pull the user record out of a GoTrue response."""
    candidate = payload.get("user")
    if not isinstance(candidate, dict):
        session = payload.get("session")
        candidate = session.get("user") if isinstance(session, dict) else None
    if not isinstance(candidate, dict) and "id" in payload:
        candidate = payload
    if not isinstance(candidate, dict) or not candidate.get("id"):
        return None
    return to_auth_user(candidate)


def to_auth_user(data: dict[str, Any]) -> AuthUser:
    """Convert a raw GoTrue user object into the safe API model."""
    return AuthUser(
        id=data["id"],
        email=data.get("email"),
        phone=data.get("phone"),
        role=data.get("role"),
        email_confirmed_at=data.get("email_confirmed_at"),
        created_at=data.get("created_at"),
        last_sign_in_at=data.get("last_sign_in_at"),
        user_metadata=data.get("user_metadata") or {},
    )


async def verify_access_token(
    auth: SupabaseAuthClient, access_token: str
) -> AuthUser:
    """Verify a bearer token with GoTrue and return the authenticated user."""
    if not access_token.strip():
        raise AuthenticationError(
            "Missing access token.", headers={"WWW-Authenticate": "Bearer"}
        )
    data = await auth.get_user(access_token)
    return to_auth_user(data)


class AuthService:
    """Sign-up, login, refresh, logout and profile operations."""

    def __init__(self, *, auth: SupabaseAuthClient, profiles: ProfileRepository) -> None:
        self._auth = auth
        self._profiles = profiles

    async def sign_up(self, payload: SignUpRequest) -> AuthResponse:
        data = await self._auth.sign_up(
            email=str(payload.email),
            password=payload.password,
            full_name=payload.full_name,
        )
        user = extract_user(data)
        if user is None:
            raise ExternalServiceError(
                "The authentication provider did not return a user record."
            )
        session = extract_session(data)

        # The DB trigger normally creates the profile. This upsert is an
        # idempotent fallback; a profile failure must not fail the signup.
        try:
            await self._profiles.upsert(user.id, full_name=payload.full_name)
        except Exception as exc:  # noqa: BLE001 - deliberate degradation
            logger.warning(
                "Profile upsert after signup failed (%s); trigger/fallback will retry",
                type(exc).__name__,
            )

        message = (
            "Account created and signed in."
            if session is not None
            else "Account created. Confirm your email address before signing in."
        )
        return AuthResponse(user=user, session=session, message=message)

    async def sign_in(self, *, email: str, password: str) -> AuthResponse:
        data = await self._auth.sign_in(email=email, password=password)
        session = extract_session(data)
        if session is None:
            raise ExternalServiceError(
                "The authentication provider did not issue a session."
            )
        user = extract_user(data) or await verify_access_token(
            self._auth, session.access_token
        )
        return AuthResponse(user=user, session=session, message="Signed in successfully.")

    async def refresh(self, refresh_token: str) -> AuthResponse:
        data = await self._auth.refresh(refresh_token)
        session = extract_session(data)
        if session is None:
            raise AuthenticationError(
                "Could not refresh the session. Please sign in again."
            )
        user = extract_user(data) or await verify_access_token(
            self._auth, session.access_token
        )
        return AuthResponse(user=user, session=session, message="Session refreshed.")

    async def logout(self, access_token: str) -> None:
        await self._auth.logout(access_token)

    async def get_me(self, user: AuthUser) -> MeResponse:
        profile_data = None
        try:
            profile_data = await self._profiles.get(user.id)
        except (ExternalServiceError, ConfigurationError, ServiceUnavailableError):
            logger.warning("Profile lookup failed; falling back to token metadata.")
            profile_data = None

        if profile_data is None:
            metadata_name = user.user_metadata.get("full_name")
            try:
                profile_data = await self._profiles.upsert(
                    user.id,
                    full_name=metadata_name if isinstance(metadata_name, str) else None,
                )
            except (ExternalServiceError, ConfigurationError, ServiceUnavailableError):
                profile_data = None

        return MeResponse(
            user=user,
            profile=Profile.model_validate(profile_data) if profile_data else None,
        )

    async def update_profile(
        self, user_id: UUID, payload: UpdateProfileRequest
    ) -> Profile:
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationAppError("No profile fields supplied.")
        profile_data = await self._profiles.update(user_id, changes)
        if profile_data is None:
            raise ExternalServiceError("The profile could not be updated.")
        return Profile.model_validate(profile_data)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
