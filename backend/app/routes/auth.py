"""Authentication endpoints: signup, login, refresh, logout, profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.dependencies import (
    AccessToken,
    AuthServiceDep,
    CurrentUser,
    rate_limit,
)
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    MessageResponse,
    Profile,
    RefreshRequest,
    SignUpRequest,
    UpdateProfileRequest,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_AUTH_RESPONSES = {
    422: {"description": "Validation error"},
    429: {"description": "Too many attempts"},
    502: {"description": "Authentication provider failure"},
    503: {"description": "Authentication not configured or unavailable"},
}


@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    description=(
        "Creates a Supabase user and returns a session when email confirmation is "
        "disabled. `user_id` is assigned by Supabase and cannot be supplied by the client."
    ),
    responses={409: {"description": "Email already registered"}, **_AUTH_RESPONSES},
    dependencies=[Depends(rate_limit("signup", "auth_signup_rate_limit_per_minute"))],
)
async def sign_up(payload: SignUpRequest, service: AuthServiceDep) -> AuthResponse:
    return await service.sign_up(payload)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Sign in",
    description=(
        "Exchanges email and password for a Supabase access/refresh token pair. "
        "Failures always return a generic message to avoid account enumeration."
    ),
    responses={401: {"description": "Invalid credentials"}, **_AUTH_RESPONSES},
    dependencies=[Depends(rate_limit("login", "auth_login_rate_limit_per_minute"))],
)
async def sign_in(payload: LoginRequest, service: AuthServiceDep) -> AuthResponse:
    return await service.sign_in(email=str(payload.email), password=payload.password)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Refresh a session",
    description="Exchanges a refresh token for a new access/refresh token pair.",
    responses={401: {"description": "Invalid refresh token"}, **_AUTH_RESPONSES},
    dependencies=[Depends(rate_limit("refresh", "auth_refresh_rate_limit_per_minute"))],
)
async def refresh_session(
    payload: RefreshRequest, service: AuthServiceDep
) -> AuthResponse:
    return await service.refresh(payload.refresh_token)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Sign out",
    description="Revokes the current Supabase session. Requires a valid access token.",
    responses={401: {"description": "Missing or invalid token"}, **_AUTH_RESPONSES},
)
async def logout(
    access_token: AccessToken,
    _current_user: CurrentUser,
    service: AuthServiceDep,
) -> MessageResponse:
    await service.logout(access_token)
    return MessageResponse(message="Signed out successfully.")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user and profile",
    description="Returns the authenticated user plus their profile row.",
    responses={401: {"description": "Missing or invalid token"}, **_AUTH_RESPONSES},
)
async def get_me(current_user: CurrentUser, service: AuthServiceDep) -> MeResponse:
    return await service.get_me(current_user)


@router.patch(
    "/me",
    response_model=Profile,
    summary="Update profile",
    description="Updates the authenticated user's profile. The user id is taken from the token.",
    responses={401: {"description": "Missing or invalid token"}, **_AUTH_RESPONSES},
)
async def update_me(
    payload: UpdateProfileRequest,
    current_user: CurrentUser,
    service: AuthServiceDep,
) -> Profile:
    return await service.update_profile(current_user.id, payload)
