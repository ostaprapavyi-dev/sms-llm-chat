"""Unit tests for the LLM abstraction: mock behaviour, factory wiring, error mapping."""

from __future__ import annotations

import pytest
from openai import APIConnectionError

from app.config import Settings
from app.core.errors import ConfigurationError, LLMError
from app.providers.llm.base import ChatMessage, LLMProvider
from app.providers.llm.factory import build_llm_provider
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


class _FakeCompletions:
    """Stands in for ``client.chat.completions`` without any network access."""

    def __init__(self, *, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error

        class _Message:
            content = self._content

        class _Choice:
            message = _Message()

        class _Completion:
            choices = [_Choice()]

        return _Completion()


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("_Chat", (), {"completions": completions})()


def _provider(completions: _FakeCompletions) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="groq",
        api_key="test-key",
        model="llama-3.3-70b-versatile",
        client=_FakeClient(completions),
    )


class TestMockProvider:
    async def test_matches_keyword_rule(self):
        provider = MockLLMProvider()
        reply = await provider.generate(
            [ChatMessage(role="user", content="How do I reset my password?")]
        )
        assert reply == (
            "You can reset your password by clicking 'Forgot password' on the login page."
        )

    async def test_is_deterministic(self):
        provider = MockLLMProvider()
        messages = [ChatMessage(role="user", content="What are your support hours?")]
        assert await provider.generate(messages) == await provider.generate(messages)

    async def test_falls_back_to_default_reply(self):
        provider = MockLLMProvider()
        reply = await provider.generate([ChatMessage(role="user", content="xyzzy")])
        assert reply.startswith("Thanks for your message!")

    async def test_answers_the_latest_user_turn(self):
        provider = MockLLMProvider()
        reply = await provider.generate(
            [
                ChatMessage(role="user", content="hello"),
                ChatMessage(role="assistant", content="Hi!"),
                ChatMessage(role="user", content="I need an invoice"),
            ]
        )
        assert "Billing" in reply

    async def test_records_calls_for_assertions(self):
        provider = MockLLMProvider()
        await provider.generate([ChatMessage(role="user", content="hi")], system="be brief")
        assert provider.calls[0][1] == "be brief"

    def test_satisfies_the_protocol(self):
        assert isinstance(MockLLMProvider(), LLMProvider)


class TestOpenAICompatibleProvider:
    async def test_returns_stripped_content(self):
        provider = _provider(_FakeCompletions(content="  Reset it from the login page.  "))
        assert await provider.generate([ChatMessage(role="user", content="help")]) == (
            "Reset it from the login page."
        )

    async def test_prepends_the_system_prompt(self):
        completions = _FakeCompletions(content="ok")
        await _provider(completions).generate(
            [ChatMessage(role="user", content="help")], system="be brief"
        )
        assert completions.last_kwargs["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "help"},
        ]

    async def test_wraps_vendor_errors_in_llm_error(self):
        error = APIConnectionError(request=None)
        provider = _provider(_FakeCompletions(error=error))
        with pytest.raises(LLMError):
            await provider.generate([ChatMessage(role="user", content="help")])

    async def test_rejects_an_empty_completion(self):
        provider = _provider(_FakeCompletions(content="   "))
        with pytest.raises(LLMError):
            await provider.generate([ChatMessage(role="user", content="help")])


class TestFactory:
    def test_builds_the_mock_provider_by_default(self):
        assert isinstance(build_llm_provider(Settings(_env_file=None)), MockLLMProvider)

    def test_builds_groq_with_its_preset(self):
        settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="key")
        provider = build_llm_provider(settings)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert (provider.name, provider.model) == ("groq", "llama-3.3-70b-versatile")

    def test_builds_openai_with_its_preset(self):
        settings = Settings(_env_file=None, llm_provider="openai", openai_api_key="key")
        provider = build_llm_provider(settings)
        assert (provider.name, provider.model) == ("openai", "gpt-4o-mini")

    def test_explicit_model_overrides_the_preset(self):
        settings = Settings(
            _env_file=None, llm_provider="groq", groq_api_key="key", llm_model="custom-model"
        )
        assert build_llm_provider(settings).model == "custom-model"

    def test_missing_api_key_is_a_configuration_error(self):
        settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="")
        with pytest.raises(ConfigurationError):
            build_llm_provider(settings)
