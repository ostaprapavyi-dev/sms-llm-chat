"""Admin API: read the stored conversations of one phone number."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, RepositoryDep
from app.core.phone import normalize_phone
from app.core.schemas import CamelModel
from app.domain.models import ConversationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class ConversationListResponse(CamelModel):
    phone_number: str
    count: int
    conversations: list[ConversationResponse]


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="All conversations of one phone number (admin only)",
)
async def list_conversations(
    admin: AdminDep,
    repository: RepositoryDep,
    phone_number: str = Query(
        alias="phoneNumber",
        description="Sender number in any common format; normalised to E.164.",
        examples=["+36123456789"],
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ConversationListResponse:
    normalized = normalize_phone(phone_number)
    conversations = await repository.list_by_phone(normalized, limit=limit, offset=offset)

    logger.info(
        "admin read conversations",
        extra={"admin": admin.username, "phoneNumber": normalized, "count": len(conversations)},
    )
    return ConversationListResponse(
        phone_number=normalized,
        count=len(conversations),
        conversations=[ConversationResponse.model_validate(item) for item in conversations],
    )
