"""FastAPI dependencies.

Providers and the repository are built once at startup and kept on ``app.state``; the
dependencies below just hand them out. Tests replace any of them through
``app.dependency_overrides`` without touching application code.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.db.repository import ConversationRepository
from app.providers.llm.base import LLMProvider
from app.providers.sms.base import SmsProvider
from app.providers.sms.twilio import TwilioProvider
from app.services.conversation_service import ConversationService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> ConversationRepository:
    return request.app.state.repository


def get_llm_provider(request: Request) -> LLMProvider:
    return request.app.state.llm_provider


def get_sms_provider(request: Request) -> SmsProvider:
    return request.app.state.sms_provider


def get_twilio_webhook_parser(request: Request) -> TwilioProvider:
    """Parser for the Twilio webhook route.

    The wire format of a route is fixed, but the *sending* provider is configurable, so
    the Twilio endpoint keeps working (parsing and signature checks) even when replies
    go out through the mock provider.
    """
    return request.app.state.twilio_parser


def get_conversation_service(
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[ConversationRepository, Depends(get_repository)],
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
    sms: Annotated[SmsProvider, Depends(get_sms_provider)],
) -> ConversationService:
    return ConversationService(repository=repository, llm=llm, sms=sms, settings=settings)


SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[ConversationRepository, Depends(get_repository)]
SmsProviderDep = Annotated[SmsProvider, Depends(get_sms_provider)]
TwilioParserDep = Annotated[TwilioProvider, Depends(get_twilio_webhook_parser)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
