"""Application configuration.

All configuration is supplied through environment variables (optionally loaded
from a local ``.env`` file). Secrets are wrapped in :class:`pydantic.SecretStr`
so they are never rendered accidentally in logs, tracebacks or health payloads.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]

_PLACEHOLDER_VALUES = {
    "",
    "your-roboflow-private-api-key",
    "your-supabase-publishable-key",
    "your-supabase-secret-key",
    "your-groq-api-key",
    "changeme",
    "replace-me",
}


def _is_real_secret(secret: SecretStr | None) -> bool:
    """Return ``True`` only when a secret is present and not a template value."""
    if secret is None:
        return False
    try:
        value = secret.get_secret_value().strip()
    except Exception:  # pragma: no cover - defensive, SecretStr always works
        return False
    return value.lower() not in _PLACEHOLDER_VALUES


class Settings(BaseSettings):
    """Typed, validated application settings.

    Field names map case-insensitively to environment variables, e.g.
    ``roboflow_api_key`` reads ``ROBOFLOW_API_KEY``.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ---------------------------------------------------------
    app_name: str = "CarDamageAI API"
    app_version: str = "0.1.0"
    environment: Environment = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://dentsense-frontend.vercel.app",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # -- Roboflow ------------------------------------------------------------
    roboflow_api_key: SecretStr | None = None
    roboflow_model_id: str = "car-damage-wpmh2/1"
    roboflow_api_url: str = "https://serverless.roboflow.com"
    roboflow_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    # -- Groq (AI repair cost estimation) ------------------------------------
    groq_api_key: SecretStr | None = None
    groq_api_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    pricing_rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    message_rate_limit_per_minute: int = Field(default=10, ge=1, le=100)

    # -- Uploads -------------------------------------------------------------
    max_upload_size_mb: int = Field(default=10, gt=0, le=50)
    max_image_pixels: int = Field(default=50_000_000, ge=1_000_000, le=200_000_000)
    damage_detect_rate_limit_per_minute: int = Field(default=10, ge=1, le=100)

    # -- Supabase ------------------------------------------------------------
    supabase_url: str = "https://ivdlmmkijtbcrzhiaypp.supabase.co"
    supabase_publishable_key: SecretStr | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_secret_key: SecretStr | None = None
    supabase_storage_bucket: str = "damage-images"
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_secret: SecretStr | None = None
    supabase_http_timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    # -- Authentication ------------------------------------------------------
    password_min_length: int = Field(default=8, ge=8, le=64)
    password_max_length: int = Field(default=72, ge=8, le=72)
    auth_signup_rate_limit_per_minute: int = Field(default=5, ge=1, le=100)
    auth_login_rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    auth_refresh_rate_limit_per_minute: int = Field(default=30, ge=1, le=300)

    # -- Nearby mechanics ----------------------------------------------------
    overpass_api_url: str = "https://overpass.kumi.systems/api/interpreter"
    osrm_api_url: str = "https://router.project-osrm.org"
    mechanic_search_radius_m: int = Field(default=10000, ge=100, le=50_000)
    mechanic_search_max_results: int = Field(default=20, ge=1, le=100)
    mechanic_http_timeout_seconds: float = Field(default=25.0, gt=0, le=120)
    mechanic_rate_limit_per_minute: int = Field(default=20, ge=1, le=100)
    geoapify_api_key: SecretStr | None = None

    # -- WhatsApp ------------------------------------------------------------
    whatsapp_default_country_code: str = "92"
    whatsapp_default_message_prefix: str = (
        "Hello, I would like a repair quotation for my vehicle based on the "
        "damage report below."
    )

    # -- Security ------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=60, ge=1, le=10_000)

    # -- Derived helpers -----------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        """Accept JSON arrays *or* comma-separated strings from the env file."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:  # pragma: no cover - config error
                    raise ValueError("CORS_ORIGINS must be a JSON array or CSV") from exc
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS must be a JSON array or CSV")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator(
        "supabase_url",
        "roboflow_api_url",
        "groq_api_url",
        "overpass_api_url",
        "osrm_api_url",
    )
    @classmethod
    def _strip_trailing_slash(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    # -- Capability flags (safe to expose) -----------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_url(self) -> str | None:
        """Swagger is available everywhere except production by default."""
        return None if self.is_production else "/docs"

    @property
    def roboflow_configured(self) -> bool:
        return _is_real_secret(self.roboflow_api_key)

    @property
    def groq_configured(self) -> bool:
        return _is_real_secret(self.groq_api_key)

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url) and _is_real_secret(self.supabase_secret_key)

    @property
    def supabase_auth_configured(self) -> bool:
        return bool(self.supabase_url) and (
            _is_real_secret(self.supabase_auth_key)
            or _is_real_secret(self.supabase_secret_key)
        )

    @property
    def supabase_auth_key(self) -> SecretStr | None:
        """Key used for GoTrue requests: anon JWT first, publishable key second."""
        return self.supabase_anon_key or self.supabase_publishable_key

    @property
    def auth_url(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    def safe_summary(self) -> dict[str, Any]:
        """Non-sensitive configuration summary suitable for logs/health checks."""
        return {
            "environment": self.environment,
            "debug": self.debug,
            "roboflow_model_id": self.roboflow_model_id,
            "roboflow_configured": self.roboflow_configured,
            "groq_model": self.groq_model,
            "groq_configured": self.groq_configured,
            "supabase_url": self.supabase_url,
            "supabase_configured": self.supabase_configured,
            "supabase_auth_configured": self.supabase_auth_configured,
            "auth_key_configured": _is_real_secret(self.supabase_auth_key),
            "storage_bucket": self.supabase_storage_bucket,
            "max_upload_size_mb": self.max_upload_size_mb,
            "cors_origins": self.cors_origins,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
