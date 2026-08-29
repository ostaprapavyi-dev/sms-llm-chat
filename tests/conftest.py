"""Shared fixtures: a real application instance backed by a temporary database.

The app is exercised through ASGI (no network, no server) with the mock SMS and mock
LLM providers, so the tests cover the same wiring that runs in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db.repository import ConversationRepository
from app.main import create_app
from app.providers.llm.mock import MockLLMProvider
from app.providers.sms.mock import MockSmsProvider

ADMIN_AUTH = ("admin", "password")
PHONE = "+36123456789"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        admin_username=ADMIN_AUTH[0],
        admin_password=ADMIN_AUTH[1],
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator:
    application = create_app(settings)
    # Runs the same lifespan as uvicorn: schema creation and provider wiring.
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def repository(app) -> ConversationRepository:
    return app.state.repository


@pytest.fixture
def sms(app) -> MockSmsProvider:
    return app.state.sms_provider


@pytest.fixture
def llm(app) -> MockLLMProvider:
    return app.state.llm_provider
