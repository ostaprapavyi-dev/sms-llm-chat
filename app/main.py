"""Application factory and startup wiring.

Everything the app depends on is chosen here, once: the database, the LLM provider and
the SMS provider. Nothing below this module reads configuration to decide *which*
implementation to use.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, debug, health, webhooks
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.repository import SqlAlchemyConversationRepository
from app.db.session import Database
from app.providers.llm.factory import build_llm_provider
from app.providers.sms.factory import build_sms_provider
from app.providers.sms.twilio import TwilioProvider

logger = logging.getLogger(__name__)


def _build_twilio_parser(settings: Settings, sms_provider) -> TwilioProvider:
    """Reuse the configured Twilio provider, or build a parse-only one.

    Signature validation switches itself off when no auth token is configured, which is
    what lets the Twilio endpoint be exercised locally against the mock sender.
    """
    if isinstance(sms_provider, TwilioProvider):
        return sms_provider
    return TwilioProvider(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_phone_number,
        validate_signature=settings.twilio_validate_signature,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_url)
        await database.create_schema()

        app.state.settings = settings
        app.state.database = database
        app.state.repository = SqlAlchemyConversationRepository(database.session_factory)
        app.state.llm_provider = build_llm_provider(settings)
        app.state.sms_provider = build_sms_provider(settings)
        app.state.twilio_parser = _build_twilio_parser(settings, app.state.sms_provider)

        logger.info(
            "application started",
            extra={
                "smsProvider": settings.sms_provider,
                "llmProvider": settings.llm_provider,
                "replyMode": settings.sms_reply_mode,
            },
        )
        try:
            yield
        finally:
            await database.dispose()
            logger.info("application stopped")

    app = FastAPI(
        title="SMS + LLM Chat",
        description="Answers incoming SMS messages with LLM-generated replies.",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Settings are also set outside the lifespan so dependencies work in unit tests
    # that never trigger startup.
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    if settings.debug_endpoints_enabled:
        app.include_router(debug.router)

    return app


app = create_app()
