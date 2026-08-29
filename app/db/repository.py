"""Storage abstraction for conversations.

:class:`ConversationRepository` is the only storage contract the services know about.
Two implementations ship with the project: SQLAlchemy (production) and in-memory
(fast unit tests without a database).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import DuplicateMessageError, NotFoundError, StorageError
from app.db.models import ConversationRow
from app.domain.models import Conversation, ConversationUpdate, utcnow

logger = logging.getLogger(__name__)


@runtime_checkable
class ConversationRepository(Protocol):
    """Everything the services need from storage -- nothing more."""

    async def create(self, conversation: Conversation) -> Conversation:
        """Persist a new conversation.

        Raises DuplicateMessageError if provider_message_id was already stored.
        """
        ...

    async def update(self, conversation_id: str, changes: ConversationUpdate) -> Conversation: ...

    async def get(self, conversation_id: str) -> Conversation | None: ...

    async def get_by_provider_message_id(self, provider_message_id: str) -> Conversation | None: ...

    async def get_by_feedback_message_id(
        self, provider_message_id: str
    ) -> Conversation | None:
        """The conversation a rating from this inbound SMS was already applied to."""
        ...

    async def get_latest_for_phone(self, phone_number: str) -> Conversation | None: ...

    async def list_by_phone(
        self, phone_number: str, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        """Newest first -- the admin view."""
        ...

    async def recent_for_phone(self, phone_number: str, *, limit: int = 10) -> list[Conversation]:
        """Oldest first -- the dialogue history handed to the LLM."""
        ...


def _to_domain(row: ConversationRow) -> Conversation:
    conversation = Conversation.model_validate(row)
    # SQLite drops tzinfo on the way out; the domain always speaks UTC.
    if conversation.created_at.tzinfo is None:
        conversation.created_at = conversation.created_at.replace(tzinfo=UTC)
    if conversation.updated_at.tzinfo is None:
        conversation.updated_at = conversation.updated_at.replace(tzinfo=UTC)
    return conversation


class SqlAlchemyConversationRepository:
    """Repository backed by any SQLAlchemy async dialect (SQLite, PostgreSQL, ...).

    A session is opened per operation: background tasks outlive the request that
    started them, so there is no request-scoped session to piggyback on.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create(self, conversation: Conversation) -> Conversation:
        row = ConversationRow(**conversation.model_dump())
        try:
            async with self._session_factory() as session, session.begin():
                session.add(row)
        except IntegrityError as exc:
            raise DuplicateMessageError(
                f"Message {conversation.provider_message_id} was already processed"
            ) from exc
        except SQLAlchemyError as exc:
            raise StorageError("Could not store the conversation") from exc
        return _to_domain(row)

    async def update(self, conversation_id: str, changes: ConversationUpdate) -> Conversation:
        values = changes.changes()
        try:
            async with self._session_factory() as session, session.begin():
                row = await session.get(ConversationRow, conversation_id)
                if row is None:
                    raise NotFoundError(f"Conversation {conversation_id} not found")
                for field, value in values.items():
                    setattr(row, field, value)
                row.updated_at = utcnow()
        except IntegrityError as exc:
            # Only the unique feedback_message_id can collide here.
            raise DuplicateMessageError("This feedback message was already applied") from exc
        except SQLAlchemyError as exc:
            raise StorageError("Could not update the conversation") from exc
        return _to_domain(row)

    async def get(self, conversation_id: str) -> Conversation | None:
        return await self._first(
            select(ConversationRow).where(ConversationRow.id == conversation_id)
        )

    async def get_by_provider_message_id(self, provider_message_id: str) -> Conversation | None:
        return await self._first(
            select(ConversationRow).where(
                ConversationRow.provider_message_id == provider_message_id
            )
        )

    async def get_by_feedback_message_id(self, provider_message_id: str) -> Conversation | None:
        return await self._first(
            select(ConversationRow).where(
                ConversationRow.feedback_message_id == provider_message_id
            )
        )

    async def get_latest_for_phone(self, phone_number: str) -> Conversation | None:
        matches = await self.list_by_phone(phone_number, limit=1)
        return matches[0] if matches else None

    async def list_by_phone(
        self, phone_number: str, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        return await self._all(
            select(ConversationRow)
            .where(ConversationRow.phone_number == phone_number)
            .order_by(ConversationRow.created_at.desc(), ConversationRow.id.desc())
            .limit(limit)
            .offset(offset)
        )

    async def recent_for_phone(self, phone_number: str, *, limit: int = 10) -> list[Conversation]:
        newest_first = await self.list_by_phone(phone_number, limit=limit)
        return list(reversed(newest_first))

    async def _first(self, stmt) -> Conversation | None:
        rows = await self._all(stmt.limit(1))
        return rows[0] if rows else None

    async def _all(self, stmt) -> list[Conversation]:
        try:
            async with self._session_factory() as session:
                result = await session.execute(stmt)
                return [_to_domain(row) for row in result.scalars().all()]
        except SQLAlchemyError as exc:
            raise StorageError("Could not read conversations") from exc


class InMemoryConversationRepository:
    """Dictionary-backed repository used by unit tests and local experiments."""

    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}
        self._lock = asyncio.Lock()

    async def create(self, conversation: Conversation) -> Conversation:
        async with self._lock:
            if any(
                item.provider_message_id == conversation.provider_message_id
                for item in self._items.values()
            ):
                raise DuplicateMessageError(
                    f"Message {conversation.provider_message_id} was already processed"
                )
            stored = conversation.model_copy(deep=True)
            self._items[stored.id] = stored
            return stored.model_copy(deep=True)

    async def update(self, conversation_id: str, changes: ConversationUpdate) -> Conversation:
        async with self._lock:
            stored = self._items.get(conversation_id)
            if stored is None:
                raise NotFoundError(f"Conversation {conversation_id} not found")
            feedback_id = changes.changes().get("feedback_message_id")
            if feedback_id is not None and any(
                item.feedback_message_id == feedback_id and item.id != conversation_id
                for item in self._items.values()
            ):
                raise DuplicateMessageError("This feedback message was already applied")
            updated = stored.model_copy(update={**changes.changes(), "updated_at": utcnow()})
            self._items[conversation_id] = updated
            return updated.model_copy(deep=True)

    async def get(self, conversation_id: str) -> Conversation | None:
        stored = self._items.get(conversation_id)
        return stored.model_copy(deep=True) if stored else None

    async def get_by_provider_message_id(self, provider_message_id: str) -> Conversation | None:
        for item in self._sorted():
            if item.provider_message_id == provider_message_id:
                return item.model_copy(deep=True)
        return None

    async def get_by_feedback_message_id(self, provider_message_id: str) -> Conversation | None:
        for item in self._sorted():
            if item.feedback_message_id == provider_message_id:
                return item.model_copy(deep=True)
        return None

    async def get_latest_for_phone(self, phone_number: str) -> Conversation | None:
        matches = await self.list_by_phone(phone_number, limit=1)
        return matches[0] if matches else None

    async def list_by_phone(
        self, phone_number: str, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        matches = [item for item in self._sorted() if item.phone_number == phone_number]
        return [item.model_copy(deep=True) for item in matches[offset : offset + limit]]

    async def recent_for_phone(self, phone_number: str, *, limit: int = 10) -> list[Conversation]:
        newest_first = await self.list_by_phone(phone_number, limit=limit)
        return list(reversed(newest_first))

    def _sorted(self) -> list[Conversation]:
        return sorted(
            self._items.values(), key=lambda item: (item.created_at, item.id), reverse=True
        )
