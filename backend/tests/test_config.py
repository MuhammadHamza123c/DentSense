"""Configuration parsing and capability-flag tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_are_safe() -> None:
    settings = _settings()

    assert settings.environment == "development"
    assert settings.roboflow_model_id == "car-damage-wpmh2/1"
    assert settings.roboflow_api_url == "https://serverless.roboflow.com"
    assert settings.supabase_url == "https://ivdlmmkijtbcrzhiaypp.supabase.co"
    assert settings.max_upload_size_bytes == 10 * 1024 * 1024
    assert settings.roboflow_configured is False
    assert settings.supabase_configured is False


def test_cors_origins_accept_csv() -> None:
    settings = _settings(cors_origins="http://a.test, http://b.test")

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_accept_json() -> None:
    settings = _settings(cors_origins='["http://a.test", "http://b.test"]')

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_reject_invalid_json() -> None:
    with pytest.raises(ValidationError):
        _settings(cors_origins="[not-json")


def test_placeholder_credentials_are_not_configured() -> None:
    settings = _settings(
        roboflow_api_key="your-roboflow-private-api-key",
        supabase_secret_key="your-supabase-secret-key",
    )

    assert settings.roboflow_configured is False
    assert settings.supabase_configured is False


def test_real_credentials_are_configured() -> None:
    settings = _settings(
        roboflow_api_key="rf_live_abcdef123456",
        supabase_secret_key="sb_secret_abcdef123456",
    )

    assert settings.roboflow_configured is True
    assert settings.supabase_configured is True


def test_docs_disabled_in_production() -> None:
    assert _settings(environment="production").docs_url is None
    assert _settings(environment="development").docs_url == "/docs"


def test_urls_have_no_trailing_slash() -> None:
    settings = _settings(roboflow_api_url="https://example.test/")

    assert settings.roboflow_api_url == "https://example.test"


def test_upload_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        _settings(max_upload_size_mb=0)
    with pytest.raises(ValidationError):
        _settings(max_upload_size_mb=51)


def test_safe_summary_excludes_secrets() -> None:
    settings = _settings(
        roboflow_api_key="rf_live_supersecretvalue",
        supabase_secret_key="sb_secret_supersecretvalue",
    )

    summary = str(settings.safe_summary())
    assert "supersecretvalue" not in summary
    assert summary.count("roboflow_configured") == 1
