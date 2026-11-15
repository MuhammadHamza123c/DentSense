"""Shared pytest fixtures.

External services are never called by the test suite: settings fixtures leave
credentials unset and individual tests inject fake clients instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Deterministic settings isolated from any developer ``.env`` file."""
    return Settings(
        _env_file=None,
        environment="test",
        debug=False,
        log_level="WARNING",
        roboflow_api_key=None,
        supabase_secret_key=None,
        supabase_publishable_key=None,
        max_upload_size_mb=2,
        rate_limit_enabled=False,
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def configured_settings() -> Settings:
    """Settings with placeholder-free credentials for capability checks."""
    return Settings(
        _env_file=None,
        environment="test",
        log_level="WARNING",
        roboflow_api_key="rf_test_key_1234567890",
        supabase_secret_key="sb_secret_test_1234567890",
        supabase_publishable_key="sb_publishable_test_1234567890",
        rate_limit_enabled=False,
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    app = create_app(test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
