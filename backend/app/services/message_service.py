"""AI-generated damage message (Roman Urdu) via Groq."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a car owner whose car is damaged. Write a WhatsApp message in Roman Urdu "
    "(Urdu written in Latin script) addressed to a mechanic (use the mechanic's name "
    "if provided). Tell him: the car is damaged, which parts are damaged (list the "
    "parts — use what the user reports), mention the car's brand/company if given, "
    "that you are sending photos, and ask for his estimate and availability. Around "
    "100–120 words. Be natural and polite, like a real car owner talking to a "
    "mechanic. Include the mechanic's name once at the start. No long backstory, but "
    "be detailed enough that the mechanic knows what is wrong. Return only the "
    "message text, with no headings, no bullets, no labels."
)


class MessageService:
    """Generates a Roman-Urdu damage message with Groq."""

    def __init__(self, settings: Settings, client_factory: Any | None = None) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def make_message(
        self,
        *,
        damage_classes: list[str],
        lat: float | None,
        lng: float | None,
        image_url: str | None,
        mechanic_name: str | None = None,
        car_brand: str | None = None,
    ) -> str:
        if not self._settings.groq_configured:
            raise ConfigurationError(
                "Message generation is not configured on the server (GROQ_API_KEY)."
            )

        damages = ", ".join(damage_classes) or "damage"
        target = f"Mechanic name: {mechanic_name}." if mechanic_name else "Mechanic name: not selected — use a generic greeting."
        brand = f"Car brand/company: {car_brand}." if car_brand else "Car brand/company: not given — do not mention it."
        user_prompt = (
            f"{target}\n"
            f"{brand}\n"
            f"Damaged parts: {damages}.\n"
            "Write the message now."
        )

        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                client.post(
                    "/chat/completions",
                    json={
                        "model": self._settings.groq_model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                    },
                ),
                timeout=self._settings.groq_timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("Groq message request timed out")
            raise ServiceUnavailableError(
                "Message generation timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Groq message network error: %s", type(exc).__name__)
            raise ExternalServiceError(
                "The message service is unreachable. Please try again."
            ) from exc

        if response.status_code >= 400:
            raise _map_groq_error(response)

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError(
                "Message generation returned an invalid response."
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ExternalServiceError("Message generation returned an empty response.")

        logger.info(
            "Groq message completed in %.1f ms", (time.perf_counter() - started) * 1000
        )
        return content.strip()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    if self._client_factory is not None:
                        self._client = self._client_factory()
                    else:
                        key = self._settings.groq_api_key
                        if key is None:
                            raise ConfigurationError(
                                "Message generation is not configured on the server "
                                "(GROQ_API_KEY)."
                            )
                        self._client = httpx.AsyncClient(
                            base_url=self._settings.groq_api_url,
                            timeout=httpx.Timeout(self._settings.groq_timeout_seconds),
                            headers={
                                "Authorization": f"Bearer {key.get_secret_value()}",
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                            },
                        )
                    logger.info(
                        "Groq message client initialised (model=%s)",
                        self._settings.groq_model,
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _map_groq_error(response: httpx.Response) -> Exception:
    status = response.status_code
    logger.warning("Groq message request failed: status=%s", status)
    if status in (401, 403):
        return ExternalServiceError(
            "Message generation rejected the server credentials. Contact the administrator."
        )
    if status == 429:
        return ServiceUnavailableError(
            "Message generation is rate-limited. Please try again shortly.",
            headers={"Retry-After": "30"},
        )
    if status >= 500:
        return ServiceUnavailableError("Message generation is temporarily unavailable.")
    return ExternalServiceError("Message generation failed. Please try again.")
