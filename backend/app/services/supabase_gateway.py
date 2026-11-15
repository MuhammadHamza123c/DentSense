"""Lazily-created, lifecycle-managed Supabase clients."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.exceptions import ConfigurationError
from app.logging_config import get_logger
from app.services.supabase_auth import SupabaseAuthClient
from app.services.supabase_rest import SupabaseRestClient
from app.services.supabase_storage import SupabaseStorageClient

logger = get_logger(__name__)


class SupabaseGateway:
    """Owns the pooled HTTP clients used to talk to Supabase.

    Clients are created on first use so that the API can boot (and expose
    ``/health``) even when Supabase credentials are not configured.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._auth: SupabaseAuthClient | None = None
        self._rest: SupabaseRestClient | None = None
        self._storage: SupabaseStorageClient | None = None
        self._lock = asyncio.Lock()

    async def auth(self) -> SupabaseAuthClient:
        if self._auth is None:
            async with self._lock:
                if self._auth is None:
                    api_key = self._require_key(
                        self._settings.supabase_auth_key,
                        "Supabase authentication is not configured (SUPABASE_ANON_KEY).",
                    )
                    http = httpx.AsyncClient(
                        base_url=self._settings.auth_url,
                        timeout=httpx.Timeout(self._settings.supabase_http_timeout_seconds),
                        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                        headers={
                            "apikey": api_key,
                            "Accept": "application/json",
                            "X-Client-Info": f"cardamageai-backend/{self._settings.app_version}",
                        },
                    )
                    self._auth = SupabaseAuthClient(http)
                    logger.info("Supabase Auth client initialised")
        return self._auth

    async def rest(self) -> SupabaseRestClient:
        if self._rest is None:
            async with self._lock:
                if self._rest is None:
                    secret = self._require_key(
                        self._settings.supabase_secret_key,
                        "Supabase server credentials are not configured (SUPABASE_SECRET_KEY).",
                    )
                    http = httpx.AsyncClient(
                        base_url=self._settings.rest_url,
                        timeout=httpx.Timeout(self._settings.supabase_http_timeout_seconds),
                        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                        headers={
                            "apikey": secret,
                            "Authorization": f"Bearer {secret}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "X-Client-Info": f"cardamageai-backend/{self._settings.app_version}",
                        },
                    )
                    self._rest = SupabaseRestClient(http)
                    logger.info("Supabase REST client initialised (service role; bypasses RLS)")
        return self._rest

    async def storage(self) -> SupabaseStorageClient:
        if self._storage is None:
            async with self._lock:
                if self._storage is None:
                    secret = self._require_key(
                        self._settings.supabase_secret_key,
                        "Supabase storage credentials are not configured (SUPABASE_SECRET_KEY).",
                    )
                    bucket = self._settings.supabase_storage_bucket
                    http = httpx.AsyncClient(
                        base_url=f"{self._settings.supabase_url}/storage/v1",
                        timeout=httpx.Timeout(self._settings.supabase_http_timeout_seconds),
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                        headers={
                            "apikey": secret,
                            "Authorization": f"Bearer {secret}",
                            "X-Client-Info": f"cardamageai-backend/{self._settings.app_version}",
                        },
                    )
                    self._storage = SupabaseStorageClient(
                        http,
                        bucket=bucket,
                        public_base_url=(
                            f"{self._settings.supabase_url}/storage/v1/object/public/{bucket}"
                        ),
                    )
                    logger.info("Supabase Storage client initialised (bucket=%s)", bucket)
        return self._storage

    async def close(self) -> None:
        if self._auth is not None:
            await self._auth.aclose()
            self._auth = None
        if self._rest is not None:
            await self._rest.aclose()
            self._rest = None
        if self._storage is not None:
            await self._storage.aclose()
            self._storage = None

    @staticmethod
    def _require_key(secret: object, message: str) -> str:
        from pydantic import SecretStr

        if not isinstance(secret, SecretStr):
            raise ConfigurationError(message)
        value = secret.get_secret_value().strip()
        if not value:
            raise ConfigurationError(message)
        return value
