"""FastAPI dependencies.

Providers and the repository are built once at startup and kept on ``app.state``; the
dependencies below just hand them out. Tests replace any of them through
``app.dependency_overrides`` without touching application code.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

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


class AuthenticatedUser(BaseModel):
    """Who is calling an authenticated endpoint.

    A single operator account from the environment is enough for this project; the role
    is modelled explicitly so the authorization check is a real one and swapping in a
    users table plus JWT later does not change the endpoints.
    """

    username: str
    role: str


_basic_auth = HTTPBasic(auto_error=False, description="Admin credentials")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid admin credentials",
    headers={"WWW-Authenticate": "Basic"},
)


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)] = None,
) -> AuthenticatedUser:
    """Authenticate the caller with HTTP Basic, in constant time."""
    if credentials is None:
        raise _UNAUTHORIZED

    # compare_digest on both fields: a plain == leaks which half was wrong via timing.
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise _UNAUTHORIZED

    return AuthenticatedUser(username=credentials.username, role="admin")


def require_admin(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """Authorize: authenticated is not enough, the role has to be ``admin``."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required"
        )
    return user


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
AdminDep = Annotated[AuthenticatedUser, Depends(require_admin)]
