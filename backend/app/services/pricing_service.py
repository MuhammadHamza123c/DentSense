"""Groq-backed repair cost estimation (PKR).

Only the per-damage price comes from the LLM (``openai/gpt-oss-120b``). The
area → severity mapping and the final total are computed deterministically
here, so the model cannot change the arithmetic or the item list.

Security: the Groq API key is read from settings (SecretStr) and never
returned, logged or included in error messages.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    ServiceUnavailableError,
)
from app.logging_config import get_logger
from app.schemas.pricing import (
    DamagePricingItem,
    PriceEstimateResponse,
    PricedDamage,
    Severity,
)

logger = get_logger(__name__)

# Area-based severity formula. ``damage_area_percentage`` is the damaged area
# as a percentage of the whole photo (detection box area / image area * 100).
SEVERITY_BANDS: tuple[tuple[float, Severity, float], ...] = (
    (5.0, "minor", 0.6),
    (15.0, "moderate", 1.0),
    (30.0, "severe", 1.8),
    (100.0, "critical", 2.5),
)

# Guardrail against nonsensical model output (one damage cannot cost this much).
MAX_ITEM_PRICE_PKR = 5_000_000

_SYSTEM_PROMPT = """You are an experienced auto body repair cost estimator for the Pakistani market.
Price each supplied car damage in Pakistani Rupees (PKR) for a typical sedan
(for example Suzuki, Honda or Toyota), including parts and labour.

Understand damage names by the body part they refer to:
- bonnet / bonnet-dent -> hood
- front-bumper / front-bumper-dent / damaged-front-bumper -> front bumper
- rear-bumper / rear-bumper-dent / damaged-rear-bumper -> rear bumper
- fender / fender-dent / damaged-fender -> front fender (wing)
- door / door-dent / damaged-door -> door panel
- headlight, taillight, windshield, mirror -> that part
- dent, scratch, damaged -> general body repair on the indicated area

Severity is already derived from the damaged area. Do not recalculate it and do
not change it:
- area <= 5%   -> minor    (severity_factor 0.6)
- area <= 15%  -> moderate (severity_factor 1.0)
- area <= 30%  -> severe   (severity_factor 1.8)
- area > 30%   -> critical (severity_factor 2.5)

For every item:
1. Estimate the base repair cost (parts + labour) for that class on a typical
   sedan in Pakistan.
2. Multiply that base cost by the supplied severity_factor.
3. Round the result to the nearest 500 PKR.

Return JSON only, with no markdown and no explanation, in exactly this shape:
{"prices":[{"index":0,"price":18500}]}

Include every supplied index exactly once. Never add extra items and never
omit an item."""


class PricingService:
    """Prices damage classes with Groq and adds up the total."""

    def __init__(
        self,
        settings: Settings,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def estimate(
        self, items: Sequence[DamagePricingItem]
    ) -> PriceEstimateResponse:
        """Return a price for every item plus the backend-computed total."""
        if not self._settings.groq_configured:
            raise ConfigurationError(
                "Price estimation is not configured on the server (GROQ_API_KEY)."
            )

        classified = [
            (item, *_classify_area(item.damage_area_percentage)) for item in items
        ]
        user_prompt = json.dumps(
            {
                "currency": "PKR",
                "market": "Pakistan",
                "vehicle": "typical sedan",
                "damages": [
                    {
                        "index": index,
                        "damage_class": item.damage_class,
                        "damage_area_percentage": item.damage_area_percentage,
                        "severity": severity,
                        "severity_factor": factor,
                    }
                    for index, (item, severity, factor) in enumerate(classified)
                ],
            },
            ensure_ascii=False,
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
                        "response_format": {"type": "json_object"},
                    },
                ),
                timeout=self._settings.groq_timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("Groq pricing request timed out")
            raise ServiceUnavailableError(
                "Price estimation timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Groq pricing network error: %s", type(exc).__name__)
            raise ExternalServiceError(
                "The price estimation service is unreachable. Please try again."
            ) from exc

        if response.status_code >= 400:
            raise _map_groq_error(response)

        prices = _prices_from_response(response, expected=len(classified))
        logger.info(
            "Groq pricing completed in %.1f ms (items=%d)",
            (time.perf_counter() - started) * 1000,
            len(classified),
        )

        priced = [
            PricedDamage(
                damage_class=item.damage_class,
                damage_area_percentage=item.damage_area_percentage,
                severity=severity,
                severity_factor=factor,
                price=prices[index],
            )
            for index, (item, severity, factor) in enumerate(classified)
        ]
        return PriceEstimateResponse(
            model=self._settings.groq_model,
            items=priced,
            total=sum(entry.price for entry in priced),
        )

    # -- Client handling ----------------------------------------------------
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
                                "Price estimation is not configured on the server "
                                "(GROQ_API_KEY)."
                            )
                        self._client = httpx.AsyncClient(
                            base_url=self._settings.groq_api_url,
                            timeout=httpx.Timeout(
                                self._settings.groq_timeout_seconds
                            ),
                            headers={
                                "Authorization": f"Bearer {key.get_secret_value()}",
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                                "User-Agent": (
                                    f"cardamageai-backend/{self._settings.app_version}"
                                ),
                            },
                        )
                    logger.info(
                        "Groq pricing client initialised (model=%s)",
                        self._settings.groq_model,
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _classify_area(area_percentage: float) -> tuple[Severity, float]:
    """Map damaged area (%) to (severity, multiplier)."""
    for upper_bound, severity, factor in SEVERITY_BANDS:
        if area_percentage <= upper_bound:
            return severity, factor
    return "critical", 2.5


def _prices_from_response(
    response: httpx.Response, *, expected: int
) -> dict[int, int]:
    """Extract and validate ``{index: price}`` from a chat completion."""
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ExternalServiceError(
            "Price estimation returned an invalid response."
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ExternalServiceError("Price estimation returned an empty response.")

    payload = _loads_json_object(content)
    if isinstance(payload, dict):
        entries: Any = (
            payload.get("prices")
            or payload.get("damages")
            or payload.get("items")
        )
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = None

    if isinstance(entries, dict):
        raw_items: list[tuple[Any, Any]] = list(entries.items())
    elif isinstance(entries, list):
        raw_items = [
            (entry.get("index"), entry) if isinstance(entry, dict) else (None, entry)
            for entry in entries
        ]
    else:
        raise ExternalServiceError(
            "Price estimation returned an invalid response."
        )

    prices: dict[int, int] = {}
    for key, entry in raw_items:
        if isinstance(entry, dict):
            value = (
                entry.get("price")
                if entry.get("price") is not None
                else entry.get("price_pkr")
                if entry.get("price_pkr") is not None
                else entry.get("estimated_price")
            )
            index = entry.get("index", key)
        else:
            value = entry
            index = key

        index_number = _as_index(index)
        price = _as_price(value)
        if index_number is None or price is None:
            raise ExternalServiceError(
                "Price estimation returned an invalid response."
            )
        if index_number in prices:
            raise ExternalServiceError(
                "Price estimation returned duplicate items."
            )
        prices[index_number] = price

    if len(prices) != expected or set(prices) != set(range(expected)):
        raise ExternalServiceError(
            "Price estimation did not return a price for every damage."
        )
    return prices


def _loads_json_object(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExternalServiceError(
            "Price estimation returned an invalid response."
        ) from exc


def _as_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_price(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        cleaned = value.lower().replace("pkr", "").replace(",", "").strip()
        try:
            value = float(cleaned)
        except ValueError:
            return None
    if not isinstance(value, int | float):
        return None
    price = int(round(float(value)))
    if price <= 0 or price > MAX_ITEM_PRICE_PKR:
        return None
    return price


def _map_groq_error(response: httpx.Response) -> Exception:
    """Translate Groq failures into safe API errors (no key leakage)."""
    status = response.status_code
    logger.warning("Groq pricing request failed: status=%s", status)

    if status in (401, 403):
        return ExternalServiceError(
            "Price estimation rejected the server credentials. "
            "Contact the administrator."
        )
    if status == 429:
        return ServiceUnavailableError(
            "Price estimation is rate-limited. Please try again shortly.",
            headers={"Retry-After": "30"},
        )
    if status in (400, 404, 422):
        return ExternalServiceError(
            "Price estimation could not process this request."
        )
    if status >= 500:
        return ServiceUnavailableError(
            "Price estimation is temporarily unavailable."
        )
    return ExternalServiceError("Price estimation failed. Please try again.")
