"""Minimal async client for Supabase Storage.

Images are uploaded to the configured bucket and then referenced by their public
URL (the bucket is public-read so Roboflow and WhatsApp can fetch them).
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    PayloadTooLargeError,
    ServiceUnavailableError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class SupabaseStorageClient:
    """Uploads objects and returns their public URLs."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        bucket: str,
        public_base_url: str,
    ) -> None:
        self._http = http
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")

    async def upload(
        self, *, path: str, data: bytes, content_type: str
    ) -> str:
        """Upload ``data`` to ``path`` inside the bucket and return the public URL.

        ``path`` must already be unique (e.g. ``<user_id>/<uuid>.jpg``).
        """
        encoded_path = quote(path.lstrip("/"), safe="/")
        try:
            response = await self._http.post(
                f"/object/{self._bucket}/{encoded_path}",
                content=data,
                headers={
                    "Content-Type": content_type,
                    "x-upsert": "false",
                    "Cache-Control": "max-age=3600",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning("Supabase Storage upload timed out")
            raise ServiceUnavailableError(
                "Image storage timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Supabase Storage network error: %s", type(exc).__name__
            )
            raise ExternalServiceError("Image storage is unreachable.") from exc

        if response.status_code >= 400:
            logger.error(
                "Supabase Storage upload failed: status=%s", response.status_code
            )
            raise _map_storage_error(response.status_code)

        return f"{self._public_base_url}/{encoded_path}"

    async def aclose(self) -> None:
        await self._http.aclose()


def _map_storage_error(status: int) -> Exception:
    if status in (413,):
        return PayloadTooLargeError("The image exceeds the storage size limit.")
    if status in (401, 403):
        return ExternalServiceError(
            "Image storage rejected the server credentials."
        )
    if status == 404:
        return ConfigurationError("The image storage bucket is not available.")
    if status == 429 or status >= 500:
        return ServiceUnavailableError(
            "Image storage is temporarily unavailable. Please try again."
        )
    return ExternalServiceError("The image could not be stored.")
