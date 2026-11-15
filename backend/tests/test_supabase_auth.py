"""Supabase Auth client tests using httpx.MockTransport (no network, no credits)."""

from __future__ import annotations

import httpx
import pytest

from app.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.services.auth_service import AuthService, extract_session, extract_user
from app.services.supabase_auth import SupabaseAuthClient


def _client(handler) -> SupabaseAuthClient:  # noqa: ANN001
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://example.supabase.co/auth/v1",
        transport=transport,
        headers={"apikey": "test-key"},
    )
    return SupabaseAuthClient(http)


async def test_sign_in_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["grant_type"] == "password"
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "bearer",
                "user": {"id": "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70", "email": "a@b.com"},
            },
        )

    client = _client(handler)
    payload = await client.sign_in(email="a@b.com", password="StrongPass123")

    assert payload["access_token"] == "access"
    session = extract_session(payload)
    assert session is not None
    assert session.refresh_token == "refresh"
    user = extract_user(payload)
    assert user is not None
    assert str(user.id) == "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70"


async def test_invalid_credentials_map_to_401() -> None:
    client = _client(
        lambda request: httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Invalid login credentials"},
        )
    )

    with pytest.raises(AuthenticationError):
        await client.sign_in(email="a@b.com", password="nope")


async def test_duplicate_signup_maps_to_409() -> None:
    client = _client(
        lambda request: httpx.Response(
            422, json={"code": 422, "error_code": "user_already_exists", "msg": "User already registered"}
        )
    )

    with pytest.raises(ConflictError):
        await client.sign_up(email="a@b.com", password="StrongPass123")


async def test_rate_limit_maps_to_429() -> None:
    client = _client(
        lambda request: httpx.Response(429, json={"code": 429, "msg": "over_email_send_rate_limit"})
    )

    with pytest.raises(RateLimitError) as exc_info:
        await client.sign_up(email="a@b.com", password="StrongPass123")
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


async def test_weak_password_maps_to_422() -> None:
    client = _client(
        lambda request: httpx.Response(
            422, json={"code": 422, "error_code": "weak_password", "msg": "Weak password"}
        )
    )

    with pytest.raises(ValidationAppError):
        await client.sign_up(email="a@b.com", password="12345678")


async def test_server_error_maps_to_503() -> None:
    client = _client(lambda request: httpx.Response(500, json={"msg": "boom"}))

    with pytest.raises(ServiceUnavailableError):
        await client.sign_in(email="a@b.com", password="x")


async def test_network_error_maps_to_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)

    with pytest.raises(ExternalServiceError):
        await client.sign_in(email="a@b.com", password="x")


async def test_timeout_maps_to_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler)

    with pytest.raises(ServiceUnavailableError):
        await client.get_user("some-token")


async def test_get_user_rejects_invalid_token() -> None:
    client = _client(lambda request: httpx.Response(401, json={"msg": "invalid JWT"}))

    with pytest.raises(AuthenticationError):
        await client.get_user("expired-token")


async def test_get_user_returns_user() -> None:
    client = _client(
        lambda request: httpx.Response(
            200, json={"id": "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70", "email": "a@b.com"}
        )
    )

    user = await client.get_user("valid-token")

    assert user["email"] == "a@b.com"


class _StubAuth:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.logged_out: str | None = None

    async def sign_up(self, **kwargs):  # noqa: ANN003
        return self.payload

    async def sign_in(self, **kwargs):  # noqa: ANN003
        return self.payload

    async def refresh(self, refresh_token: str) -> dict:
        return self.payload

    async def logout(self, access_token: str) -> None:
        self.logged_out = access_token

    async def get_user(self, access_token: str) -> dict:
        return {"id": "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70", "email": "a@b.com"}


class _StubProfiles:
    async def upsert(self, user_id, **kwargs):  # noqa: ANN001, ANN003
        return {"id": str(user_id), "full_name": kwargs.get("full_name")}


async def test_signup_without_session_reports_confirmation_required() -> None:
    service = AuthService(
        auth=_StubAuth({"id": "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70", "email": "a@b.com"}),
        profiles=_StubProfiles(),
    )
    from app.schemas.auth import SignUpRequest

    result = await service.sign_up(
        SignUpRequest(email="a@b.com", password="StrongPass123")
    )

    assert result.session is None
    assert "Confirm your email" in result.message


async def test_signup_returns_session_when_autoconfirmed() -> None:
    service = AuthService(
        auth=_StubAuth(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "user": {"id": "f2f1b7a6-28a4-4f72-9d5b-2e3d4c5b6a70", "email": "a@b.com"},
            }
        ),
        profiles=_StubProfiles(),
    )
    from app.schemas.auth import SignUpRequest

    result = await service.sign_up(
        SignUpRequest(email="a@b.com", password="StrongPass123", full_name="Ali")
    )

    assert result.session is not None
    assert result.user.user_metadata == {}
