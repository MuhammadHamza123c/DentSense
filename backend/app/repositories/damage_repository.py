"""Damage report + detection persistence (Supabase PostgREST).

A report is one uploaded photo; detections are the Roboflow boxes for it.
``user_id`` always comes from the verified bearer token, never from the client.
"""

from __future__ import annotations

from typing import Any, Sequence
from uuid import UUID

from app.services.supabase_rest import SupabaseRestClient

_REPORT_COLUMNS = "id,user_id,name,lat,lng,image_url,created_at,updated_at"
_DETECTION_COLUMNS = (
    "id,report_id,user_id,damage_class,confidence,class_id,detection_id,"
    "x,y,width,height,damage_area_percentage,image_url,label,created_at"
)


class DamageReportRepository:
    """Data access for ``public.damage_reports`` and ``public.damage_detections``."""

    def __init__(self, rest: SupabaseRestClient) -> None:
        self._rest = rest

    async def create_report(
        self,
        *,
        user_id: UUID,
        image_url: str,
        name: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "image_url": image_url,
        }
        cleaned_name = (name or "").strip()
        if cleaned_name:
            payload["name"] = cleaned_name[:120]
        if lat is not None:
            payload["lat"] = lat
        if lng is not None:
            payload["lng"] = lng
        return await self._rest.insert(
            "damage_reports", payload, columns=_REPORT_COLUMNS, single=True
        )

    async def create_detections(
        self,
        *,
        user_id: UUID,
        report_id: UUID,
        image_url: str | None = None,
        label: str | None = None,
        detections: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not detections:
            return []
        cleaned_label = (label or "").strip()
        rows = [
            {
                "report_id": str(report_id),
                "user_id": str(user_id),
                "image_url": image_url,
                **({"label": cleaned_label[:120]} if cleaned_label else {}),
                **detection,
            }
            for detection in detections
        ]
        return await self._rest.insert(
            "damage_detections",
            rows,
            columns=_DETECTION_COLUMNS,
            single=False,
        )

    async def get_report(
        self, *, report_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None:
        return await self._rest.select(
            "damage_reports",
            columns=_REPORT_COLUMNS,
            filters={
                "id": f"eq.{report_id}",
                "user_id": f"eq.{user_id}",
            },
            single=True,
        )

    async def get_detections(
        self, *, report_id: UUID, user_id: UUID
    ) -> list[dict[str, Any]]:
        result = await self._rest.select(
            "damage_detections",
            columns=_DETECTION_COLUMNS,
            filters={
                "report_id": f"eq.{report_id}",
                "user_id": f"eq.{user_id}",
            },
        )
        return result if isinstance(result, list) else []
