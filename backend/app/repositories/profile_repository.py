"""Profile persistence (Supabase ``public.profiles``)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.supabase_rest import SupabaseRestClient

_COLUMNS = "id,full_name,phone,created_at,updated_at"


class ProfileRepository:
    """Data access for user profiles. The caller must supply a verified user id."""

    def __init__(self, rest: SupabaseRestClient) -> None:
        self._rest = rest

    async def get(self, user_id: UUID) -> dict[str, Any] | None:
        return await self._rest.select(
            "profiles",
            columns=_COLUMNS,
            filters={"id": f"eq.{user_id}"},
            single=True,
        )

    async def upsert(
        self,
        user_id: UUID,
        *,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"id": str(user_id)}
        if full_name is not None:
            payload["full_name"] = full_name
        if phone is not None:
            payload["phone"] = phone
        return await self._rest.upsert(
            "profiles", payload, on_conflict="id", columns=_COLUMNS
        )

    async def update(
        self, user_id: UUID, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await self._rest.update(
            "profiles",
            changes,
            filters={"id": f"eq.{user_id}"},
            columns=_COLUMNS,
        )
