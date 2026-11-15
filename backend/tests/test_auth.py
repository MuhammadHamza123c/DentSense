"""Authentication route tests (all external calls are mocked)."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_auth_service, get_current_user
from app.exceptions import AuthenticationError
from app.main import create_app
from app.schemas.auth import (
    AuthResponse,
    AuthSession,
    AuthUser,
    MeResponse,
    Profile,
)

USER_ID = uuid4()


def _user() -> AuthUser:
    return AuthUser(
        id=USER_ID,
        email="driver@example.com",
        role="authenticated",
        user_metadata={"full_name": "Test Driver"},
    )


def _session() -> AuthSession:
    return AuthSession(
        access_token="access-token-for-tests",
        refresh_token="refresh-token-for-tests",
        expires_in=3600,
    )


class FakeAuthService:
    def __init__(self) -> None:
        self.logged_out_token: str | None = None
        self.updated: dict[str, object] | None = None

    async def sign_up(self, payload):  # noqa: ANN001
        return AuthResponse(
            user=_user(), session=_session(), message="Account created and signed in."
        )

    async def sign_in(self, *, email: str, password: str) -> AuthResponse:
        if password == "wrong-password":
            raise AuthenticationError(
                "Invalid email or password.", headers={"WWW-Authenticate": "Bearer"}
            )
        return AuthResponse(
            user=_user(), session=_session(), message="Signed in successfully."
        )

    async def refresh(self, refresh_token: str) -> AuthResponse:
        return AuthResponse(user=_user(), session=_session(), message="Session refreshed.")

    async def logout(self, access_token: str) -> None:
        self.logged_out_token = access_token

    async def get_me(self, user: AuthUser) -> MeResponse:
        return MeResponse(
            user=user,
            profile=Profile(id=user.id, full_name="Test Driver", phone=None),
        )

    async def update_profile(self, user_id, payload):  # noqa: ANN001
        self.updated = payload.model_dump(exclude_unset=True)
        return Profile(id=user_id, full_name="New Name", phone="+923001234567")


@pytest.fixture
def fake_service() -> FakeAuthService:
    return FakeAuthService()


@pytest.fixture
def auth_client(test_settings: Settings, fake_service: FakeAuthService) -> Iterator[TestClient]:
    app = create_app(test_settings)
    app.dependency_overrides[get_auth_service] = lambda: fake_service
    app.dependency_overrides[get_current_user] = lambda: _user()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_signup_returns_session(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "Driver@Example.com",
            "password": "StrongPass123",
            "full_name": "Test Driver",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["id"] == str(USER_ID)
    assert body["session"]["access_token"] == "access-token-for-tests"
    assert "refresh_token" in body["session"]


def test_signup_rejects_client_supplied_user_id(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "driver@example.com",
            "password": "StrongPass123",
            "user_id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "password",
    ["short1", "alllowercase123", "12345678", "NoDigitsHere"],
)
def test_signup_enforces_password_policy(auth_client: TestClient, password: str) -> None:
    response = auth_client.post(
        "/api/v1/auth/signup",
        json={"email": "driver@example.com", "password": password},
    )

    assert response.status_code == 422


def test_login_rejects_invalid_credentials(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "driver@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_me_returns_profile(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer access-token-for-tests"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["full_name"] == "Test Driver"


def test_update_profile_uses_token_user(auth_client: TestClient, fake_service: FakeAuthService) -> None:
    response = auth_client.patch(
        "/api/v1/auth/me",
        json={"full_name": "New Name"},
        headers={"Authorization": "Bearer access-token-for-tests"},
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"
    assert fake_service.updated == {"full_name": "New Name"}


def test_logout_revokes_supplied_token(auth_client: TestClient, fake_service: FakeAuthService) -> None:
    response = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": "Bearer access-token-for-tests"},
    )

    assert response.status_code == 200
    assert fake_service.logged_out_token == "access-token-for-tests"


def test_update_profile_requires_a_field(auth_client: TestClient) -> None:
    response = auth_client.patch(
        "/api/v1/auth/me",
        json={},
        headers={"Authorization": "Bearer access-token-for-tests"},
    )

    assert response.status_code == 422
