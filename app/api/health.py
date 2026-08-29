"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.core.schemas import CamelModel

router = APIRouter(tags=["ops"])


class Health(CamelModel):
    status: str
    sms_provider: str
    llm_provider: str
    reply_mode: str


@router.get("/health", response_model=Health, summary="Service health and active providers")
async def health(settings: SettingsDep) -> Health:
    return Health(
        status="ok",
        sms_provider=settings.sms_provider,
        llm_provider=settings.llm_provider,
        reply_mode=settings.sms_reply_mode,
    )
