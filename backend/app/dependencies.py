"""Request-scoped dependencies: settings, Supabase clients, auth, rate limits."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.exceptions import AuthenticationError, ConfigurationError, RateLimitError
from app.repositories.damage_repository import DamageReportRepository
from app.repositories.profile_repository import ProfileRepository
from app.schemas.auth import AuthUser
from app.services.auth_service import AuthService, verify_access_token
from app.services.damage_detection import DamageDetectionService
from app.services.message_service import MessageService
from app.services.mechanic_service import MechanicService
from app.services.pricing_service import PricingService
from app.services.supabase_auth import SupabaseAuthClient
from app.services.supabase_gateway import SupabaseGateway
from app.services.supabase_storage import SupabaseStorageClient

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="SupabaseBearer",
    description="Supabase access token from signup/login. Send as: Bearer <access_token>",
)


def get_app_settings(request: Request) -> Settings:
    """Settings bound to this application instance."""
    return request.app.state.settings


def get_supabase_gateway(request: Request) -> SupabaseGateway:
    return request.app.state.supabase


def get_damage_detection_service(request: Request) -> DamageDetectionService:
    return request.app.state.damage_detector


def get_pricing_service(request: Request) -> PricingService:
    return request.app.state.pricing_service


def get_message_service(request: Request) -> MessageService:
    return request.app.state.message_service


def get_mechanic_service(request: Request) -> MechanicService:
    return request.app.state.mechanic_service


async def get_auth_client(
    gateway: Annotated[SupabaseGateway, Depends(get_supabase_gateway)],
) -> SupabaseAuthClient:
    return await gateway.auth()


async def get_storage_client(
    gateway: Annotated[SupabaseGateway, Depends(get_supabase_gateway)],
) -> SupabaseStorageClient:
    return await gateway.storage()


async def get_damage_report_repository(
    gateway: Annotated[SupabaseGateway, Depends(get_supabase_gateway)],
) -> DamageReportRepository | None:
    try:
        return DamageReportRepository(await gateway.rest())
    except ConfigurationError:
        return None


async def get_auth_service(
    gateway: Annotated[SupabaseGateway, Depends(get_supabase_gateway)],
) -> AuthService:
    """Build the auth service with both Auth (GoTrue) and REST clients."""
    return AuthService(
        auth=await gateway.auth(),
        profiles=ProfileRepository(await gateway.rest()),
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    gateway: Annotated[SupabaseGateway, Depends(get_supabase_gateway)],
) -> AuthUser:
    """Verify the bearer token with Supabase and return the authenticated user.

    The auth client is created only after a bearer token is present, so
    unauthenticated requests never depend on Supabase being configured.
    """
    if credentials is None:
        raise AuthenticationError(
            "Authentication required.", headers={"WWW-Authenticate": "Bearer"}
        )
    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError(
            "Only Bearer authentication is supported.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_access_token(await gateway.auth(), credentials.credentials)


def get_access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError(
            "Authentication required.", headers={"WWW-Authenticate": "Bearer"}
        )
    return credentials.credentials


def rate_limit(scope: str, setting_name: str) -> Callable[[Request], Awaitable[None]]:
    """Build a route dependency enforcing a per-IP sliding window."""

    async def dependency(request: Request) -> None:
        settings: Settings = request.app.state.settings
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, setting_name))
        client_host = request.client.host if request.client else "unknown"
        allowed, retry_after = request.app.state.rate_limiter.hit(
            f"{scope}:{client_host}", limit
        )
        if not allowed:
            raise RateLimitError(
                f"Too many requests. Please try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
StorageDep = Annotated[SupabaseStorageClient, Depends(get_storage_client)]
DamageReportRepositoryDep = Annotated[
    DamageReportRepository | None, Depends(get_damage_report_repository)
]
DamageDetectionServiceDep = Annotated[
    DamageDetectionService, Depends(get_damage_detection_service)
]
PricingServiceDep = Annotated[PricingService, Depends(get_pricing_service)]
MessageServiceDep = Annotated[MessageService, Depends(get_message_service)]
MechanicServiceDep = Annotated[MechanicService, Depends(get_mechanic_service)]
AccessToken = Annotated[str, Depends(get_access_token)]
