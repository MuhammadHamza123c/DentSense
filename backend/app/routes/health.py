"""Health and status endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.dependencies import SettingsDep
from app.schemas.common import CheckResult, HealthResponse, RootResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/",
    response_model=RootResponse,
    summary="API root / status",
    description="Basic service identification and links. Safe to use as a liveness probe.",
)
async def root(settings: SettingsDep) -> RootResponse:
    return RootResponse(
        service=settings.app_name,
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        api_version="v1",
        docs_url=settings.docs_url,
        message="CarDamageAI backend is running. See /docs for API documentation.",
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Reports process health plus whether optional integrations are configured. "
        "Never returns secrets or calls paid/rate-limited third-party services."
    ),
)
async def health(
    settings: SettingsDep,
) -> HealthResponse:
    checks: dict[str, CheckResult] = {
        "api": CheckResult(status="ok", detail="Application is serving requests."),
        "roboflow": (
            CheckResult(status="ok", detail="API key configured.")
            if settings.roboflow_configured
            else CheckResult(
                status="not_configured",
                detail="ROBOFLOW_API_KEY is missing; damage detection is disabled.",
            )
        ),
        "supabase": (
            CheckResult(status="ok", detail="Server credentials configured.")
            if settings.supabase_configured
            else CheckResult(
                status="not_configured",
                detail="SUPABASE_SECRET_KEY is missing; persistence is disabled.",
            )
        ),
        "auth": (
            CheckResult(status="ok", detail="Authentication key configured.")
            if settings.supabase_auth_configured
            else CheckResult(
                status="not_configured",
                detail="SUPABASE_ANON_KEY is missing; authentication is disabled.",
            )
        ),
        "storage": (
            CheckResult(
                status="ok", detail=f"Bucket '{settings.supabase_storage_bucket}' expected."
            )
            if settings.supabase_configured
            else CheckResult(
                status="not_configured", detail="Supabase Storage is not configured."
            )
        ),
    }

    degraded = any(check.status != "ok" for check in checks.values())
    return HealthResponse(
        status="degraded" if degraded else "ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(timezone.utc),
        checks=checks,
    )
