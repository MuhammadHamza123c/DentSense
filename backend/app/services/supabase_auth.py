"""Minimal async client for Supabase Auth (GoTrue REST API).

Why not ``supabase-py`` here? Its auth methods keep a session on the client
instance. A shared client would leak one user's session into another user's
request under concurrency, and creating a client per login would still require
careful teardown. The GoTrue REST endpoints are stable and explicit, so we call
them with a pooled ``httpx.AsyncClient`` and map errors ourselves.

No token or password is ever logged by this module.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

_ALREADY_EXISTS_MARKERS = (
    "already registered",
    "already exists",
    "user_already_exists",
    "email_exists",
)
_INVALID_CREDENTIALS_MARKERS = (
    "invalid login credentials",
    "invalid_credentials",
    "invalid_grant",
)
_EMAIL_NOT_CONFIRMED_MARKERS = (
    "email not confirmed",
    "email_not_confirmed",
)
_WEAK_PASSWORD_MARKERS = ("weak password", "weak_password")


class SupabaseAuthClient:
    """Thin, typed wrapper around the endpoints we use."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # -- Public API ---------------------------------------------------------
    async def sign_up(
        self, *, email: str, password: str, full_name: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"email": email, "password": password}
        if full_name:
            body["data"] = {"full_name": full_name}
        return await self._request("POST", "/signup", json=body)

    async def sign_in(self, *, email: str, password: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )

    async def logout(self, access_token: str) -> None:
        """Revoke a session. A token that is already invalid counts as logout."""
        try:
            await self._request(
                "POST",
                "/logout",
                headers={"Authorization": f"Bearer {access_token}"},
                allow_statuses={204, 401},
            )
        except AuthenticationError:
            # Already signed out or expired: the desired end state is achieved.
            logger.info("Logout called with an already-invalid token")

    async def get_user(self, access_token: str) -> dict[str, Any]:
        """Validate an access token with GoTrue and return the user record."""
        if not access_token or len(access_token) > 8192:
            raise AuthenticationError("Invalid or expired access token.")
        data = await self._request(
            "GET",
            "/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(data, dict) or "id" not in data:
            raise ExternalServiceError("Authentication provider returned an unexpected response.")
        return data

    # -- Internals ----------------------------------------------------------
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_statuses: set[int] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, path, params=params, json=json, headers=headers
            )
        except httpx.TimeoutException as exc:
            logger.warning("Supabase Auth timed out on %s %s", method, path)
            raise ServiceUnavailableError(
                "The authentication service timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Supabase Auth network error on %s %s: %s", method, path, type(exc).__name__)
            raise ExternalServiceError(
                "The authentication service is currently unreachable."
            ) from exc

        if allow_statuses and response.status_code in allow_statuses:
            if response.status_code >= 400:
                raise AuthenticationError("Invalid or expired access token.")
            return _safe_json(response)

        if response.status_code >= 400:
            raise _map_auth_error(response)

        return _safe_json(response)


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalServiceError(
            "Authentication provider returned an invalid response."
        ) from exc
    return payload if isinstance(payload, dict) else {"data": payload}


def _map_auth_error(response: httpx.Response) -> Exception:
    """Translate GoTrue errors into safe application errors."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    code = str(payload.get("error_code") or payload.get("code") or "").lower()
    message = str(
        payload.get("msg")
        or payload.get("error_description")
        or payload.get("error")
        or ""
    )
    combined = f"{code} {message}".lower()

    logger.info(
        "Supabase Auth error: status=%s code=%s",
        response.status_code,
        code or "unknown",
    )

    if response.status_code == 429 or "rate limit" in combined:
        return RateLimitError(
            "Too many authentication attempts. Please wait and try again.",
            headers={"Retry-After": "60"},
        )
    if any(marker in combined for marker in _ALREADY_EXISTS_MARKERS):
        return ConflictError("An account with this email address already exists.")
    if any(marker in combined for marker in _EMAIL_NOT_CONFIRMED_MARKERS):
        return PermissionDeniedError(
            "This email address is not confirmed. Confirm it before signing in."
        )
    if any(marker in combined for marker in _INVALID_CREDENTIALS_MARKERS):
        return AuthenticationError(
            "Invalid email or password.", headers={"WWW-Authenticate": "Bearer"}
        )
    if response.status_code in (401, 403):
        return AuthenticationError(
            "Invalid or expired access token.", headers={"WWW-Authenticate": "Bearer"}
        )
    if any(marker in combined for marker in _WEAK_PASSWORD_MARKERS):
        return ValidationAppError(
            "Password does not meet the security requirements.",
            details={"password": "Use at least 8 characters with a letter and a digit."},
        )
    if response.status_code in (400, 422):
        return ValidationAppError("The email or password is not valid.")
    if response.status_code >= 500:
        return ServiceUnavailableError(
            "The authentication service is temporarily unavailable."
        )
    return ExternalServiceError("Authentication failed. Please try again.")
