"""FastAPI application entry point for the CarDamageAI backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import Settings, get_settings
from app.exceptions import register_exception_handlers
from app.logging_config import configure_logging, get_logger
from app.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.routes import auth, damage, health, mechanic, message, pricing
from app.services.damage_detection import DamageDetectionService
from app.services.message_service import MessageService
from app.services.mechanic_service import MechanicService
from app.services.pricing_service import PricingService
from app.services.rate_limit import InMemoryRateLimiter
from app.services.supabase_gateway import SupabaseGateway

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory (also used by tests with overridden settings)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Starting %s v%s [%s]",
            settings.app_name,
            settings.app_version,
            settings.environment,
        )
        logger.info("Configuration summary: %s", settings.safe_summary())
        for name, configured in (
            ("Roboflow", settings.roboflow_configured),
            ("Groq pricing", settings.groq_configured),
            ("Supabase REST", settings.supabase_configured),
            ("Supabase Auth", settings.supabase_auth_configured),
        ):
            if not configured:
                logger.warning(
                    "%s is not configured; related endpoints will return 503.", name
                )
        yield
        await app.state.supabase.close()
        await app.state.damage_detector.aclose()
        await app.state.pricing_service.aclose()
        await app.state.message_service.aclose()
        await app.state.mechanic_service.aclose()
        logger.info("Shutting down %s", settings.app_name)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI-powered car damage detection and repair cost estimation API.\n\n"
            "**Estimates only** — detection confidence and repair costs are not "
            "verified inspections or guaranteed mechanic quotations."
        ),
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "CarDamageAI"},
        license_info={"name": "Proprietary"},
    )

    # Middleware order: last added runs first.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    # Dev only: a phone on the same Wi-Fi opens the frontend from the PC's LAN
    # IP (e.g. http://192.168.1.5:5173), so allow any http://<lan-ip> origin.
    allow_origin_regex = (
        r"http://(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?"
        if settings.environment != "production"
        else None
    )
    # The app's own frontend + local dev server are always allowed, even when a
    # CORS_ORIGINS env var overrides `settings.cors_origins` — a stale server
    # side list must never lock the deployed frontend out (preflights would
    # fail with 400 and every browser request would be blocked).
    first_party_origins = (
        "https://dentsense.vercel.app",
        "https://dentsense-frontend.vercel.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    allowed_origins = list(
        dict.fromkeys([*first_party_origins, *settings.cors_origins])
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Process-Time-ms"],
        max_age=600,
    )
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_size_bytes + 1024 * 1024
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.state.settings = settings
    app.state.supabase = SupabaseGateway(settings)
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.damage_detector = DamageDetectionService(settings)
    app.state.pricing_service = PricingService(settings)
    app.state.message_service = MessageService(settings)
    app.state.mechanic_service = MechanicService(settings)

    app.include_router(health.router)
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(damage.router, prefix=settings.api_v1_prefix)
    app.include_router(pricing.router, prefix=settings.api_v1_prefix)
    app.include_router(message.router, prefix=settings.api_v1_prefix)
    app.include_router(mechanic.router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
