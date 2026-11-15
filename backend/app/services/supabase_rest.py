"""Minimal async client for Supabase PostgREST (database REST API)."""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.exceptions import ExternalServiceError, ServiceUnavailableError
from app.logging_config import get_logger

logger = get_logger(__name__)

_SINGLE_OBJECT_ACCEPT = "application/vnd.pgrst.object+json"


class SupabaseRestClient:
    """Small, explicit wrapper around the PostgREST operations we need."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Mapping[str, str] | None = None,
        single: bool = False,
    ) -> Any:
        params: dict[str, str] = {"select": columns}
        if filters:
            params.update(filters)
        headers = {"Accept": _SINGLE_OBJECT_ACCEPT} if single else {}
        response = await self._request("GET", f"/{table}", params=params, headers=headers)
        if response.status_code == 406 and single:
            return None  # "JSON object requested, multiple/no rows returned"
        self._raise_for_status(response, table)
        return self._parse(response, single=single)

    async def upsert(
        self,
        table: str,
        payload: Mapping[str, Any] | list[Mapping[str, Any]],
        *,
        on_conflict: str | None = None,
        columns: str = "*",
    ) -> Any:
        params = {"select": columns, "on_conflict": on_conflict} if on_conflict else {"select": columns}
        headers = {
            "Prefer": "resolution=merge-duplicates,return=representation",
            "Accept": _SINGLE_OBJECT_ACCEPT,
        }
        response = await self._request(
            "POST", f"/{table}", params=params, json=payload, headers=headers
        )
        self._raise_for_status(response, table)
        return self._parse(response, single=True)

    async def update(
        self,
        table: str,
        payload: Mapping[str, Any],
        *,
        filters: Mapping[str, str],
        columns: str = "*",
    ) -> Any:
        headers = {"Prefer": "return=representation", "Accept": _SINGLE_OBJECT_ACCEPT}
        response = await self._request(
            "PATCH",
            f"/{table}",
            params={"select": columns, **dict(filters)},
            json=payload,
            headers=headers,
        )
        if response.status_code == 406:
            return None
        self._raise_for_status(response, table)
        return self._parse(response, single=True)

    async def insert(
        self,
        table: str,
        payload: Mapping[str, Any] | list[Mapping[str, Any]],
        *,
        columns: str = "*",
        single: bool = True,
    ) -> Any:
        """Insert one row (``single``) or many rows and return them."""
        headers = {
            "Prefer": "return=representation",
            "Accept": _SINGLE_OBJECT_ACCEPT if single else "application/json",
        }
        response = await self._request(
            "POST",
            f"/{table}",
            params={"select": columns},
            json=payload,
            headers=headers,
        )
        if response.status_code == 406 and single:
            return None
        self._raise_for_status(response, table)
        return self._parse(response, single=single)

    # -- Internals ----------------------------------------------------------
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            logger.warning("Supabase REST timeout on %s %s", method, path)
            raise ServiceUnavailableError(
                "The database service timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Supabase REST network error on %s %s: %s",
                method,
                path,
                type(exc).__name__,
            )
            raise ExternalServiceError("The database service is unreachable.") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response, table: str) -> None:
        if response.status_code < 400:
            return
        try:
            payload = response.json()
            code = payload.get("code", "unknown") if isinstance(payload, dict) else "unknown"
        except ValueError:
            code = "unknown"
        logger.error(
            "Supabase REST error: table=%s status=%s code=%s",
            table,
            response.status_code,
            code,
        )
        if response.status_code == 429:
            raise ServiceUnavailableError("The database is rate limited. Please retry shortly.")
        if response.status_code >= 500:
            raise ServiceUnavailableError("The database is temporarily unavailable.")
        if response.status_code in (401, 403):
            raise ExternalServiceError("The database rejected the server credentials.")
        raise ExternalServiceError("The database request could not be completed.")

    @staticmethod
    def _parse(response: httpx.Response, *, single: bool) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            data = response.json()
        except ValueError as exc:
            raise ExternalServiceError("The database returned an invalid response.") from exc
        if single and isinstance(data, list):
            return data[0] if data else None
        return data
