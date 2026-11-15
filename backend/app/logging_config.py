"""Logging configuration with request correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class _RedactionFilter(logging.Filter):
    """Best-effort removal of known secret values from log records."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 6]

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= 6 and value not in self._secrets:
            self._secrets.append(value)

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            try:
                message = record.getMessage()
            except Exception:  # pragma: no cover - malformed record
                return True
            for secret in self._secrets:
                if secret in message:
                    message = message.replace(secret, "***REDACTED***")
            record.msg = message
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-friendly log formatter for local development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | req=%(request_id)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_ctx.get()
        return super().format(record)


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Configure the root logger exactly once (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else TextFormatter())
    root.addHandler(handler)

    # Uvicorn access logs are replaced by our request middleware.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    from app.config import get_settings

    settings = get_settings()
    for secret in (
        settings.roboflow_api_key,
        settings.supabase_secret_key,
        settings.supabase_publishable_key,
        settings.supabase_jwt_secret,
    ):
        if secret is not None:
            handler.addFilter(_RedactionFilter([secret.get_secret_value()]))


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)
