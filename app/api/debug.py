"""Local development helpers.

Mounted only when ``DEBUG_ENDPOINTS_ENABLED`` is on: it exposes the mock provider's
outbox so the whole flow can be verified with curl and no carrier account.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SmsProviderDep
from app.core.errors import NotFoundError
from app.providers.sms.mock import MockSmsProvider, SentMessage

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get(
    "/outbox",
    response_model=list[SentMessage],
    summary="Messages the mock SMS provider would have delivered",
)
async def outbox(sms: SmsProviderDep) -> list[SentMessage]:
    if not isinstance(sms, MockSmsProvider):
        raise NotFoundError("The outbox is only available for the mock SMS provider")
    return sms.outbox
