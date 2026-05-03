"""Tests for MultiDelegate — multi-provider dispatch."""

import pytest

from genesis_router import load_config_from_string
from genesis_router.providers.multi import MultiDelegate
from genesis_router.types import CallResult

CONFIG = """\
providers:
  claude-sonnet:
    type: anthropic
    model: claude-sonnet-4-6
    free: false
    open_duration_s: 60
  gpt4o:
    type: openai
    model: gpt-4o
    free: false
    open_duration_s: 60
  llama:
    type: groq
    model: llama-3.1-8b
    free: true
    open_duration_s: 60
  local-llm:
    type: lmstudio
    model: mistral-small
    free: true
    open_duration_s: 60
    base_url: http://localhost:1234/v1

call_sites:
  chat:
    chain: [claude-sonnet, gpt4o, llama, local-llm]
"""


class FakeDelegate:
    """Records calls for assertions."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple] = []

    async def call(self, provider, model_id, messages, **kwargs):
        self.calls.append((provider, model_id, kwargs))
        return CallResult(
            success=True,
            content=f"[{self.name}] response",
            input_tokens=10,
            output_tokens=5,
        )


class TestMultiDelegate:
    @pytest.mark.asyncio
    async def test_dispatches_by_provider_type(self):
        config = load_config_from_string(CONFIG, check_api_keys=False)
        anthropic_d = FakeDelegate("anthropic")
        openai_d = FakeDelegate("openai")
        groq_d = FakeDelegate("groq")

        delegate = MultiDelegate(
            delegates={"anthropic": anthropic_d, "openai": openai_d, "groq": groq_d},
            config=config,
        )

        msgs = [{"role": "user", "content": "hi"}]

        # Anthropic provider → anthropic delegate
        r1 = await delegate.call("claude-sonnet", "claude-sonnet-4-6", msgs)
        assert r1.success
        assert len(anthropic_d.calls) == 1
        assert anthropic_d.calls[0][0] == "claude-sonnet"

        # OpenAI provider → openai delegate
        r2 = await delegate.call("gpt4o", "gpt-4o", msgs)
        assert r2.success
        assert len(openai_d.calls) == 1

        # Groq provider → groq delegate
        r3 = await delegate.call("llama", "llama-3.1-8b", msgs)
        assert r3.success
        assert len(groq_d.calls) == 1

    @pytest.mark.asyncio
    async def test_openai_compatible_types_fallback_to_openai(self):
        config = load_config_from_string(CONFIG, check_api_keys=False)
        openai_d = FakeDelegate("openai")

        delegate = MultiDelegate(
            delegates={"openai": openai_d},
            config=config,
        )

        # lmstudio type -> falls back to openai delegate
        msgs = [{"role": "user", "content": "hi"}]
        result = await delegate.call("local-llm", "mistral-small", msgs)
        assert result.success
        assert len(openai_d.calls) == 1

    @pytest.mark.asyncio
    async def test_missing_type_uses_fallback(self):
        config = load_config_from_string(CONFIG, check_api_keys=False)
        fallback = FakeDelegate("fallback")

        delegate = MultiDelegate(
            delegates={},  # no delegates registered
            config=config,
            fallback=fallback,
        )

        msgs = [{"role": "user", "content": "hi"}]
        result = await delegate.call(
            "claude-sonnet", "claude-sonnet-4-6", msgs,
        )
        assert result.success
        assert len(fallback.calls) == 1

    @pytest.mark.asyncio
    async def test_no_delegate_no_fallback_returns_error(self):
        config = load_config_from_string(CONFIG, check_api_keys=False)

        delegate = MultiDelegate(
            delegates={},
            config=config,
            fallback=None,
        )

        msgs = [{"role": "user", "content": "hi"}]
        result = await delegate.call(
            "claude-sonnet", "claude-sonnet-4-6", msgs,
        )
        assert not result.success
        assert "No delegate" in result.error

    @pytest.mark.asyncio
    async def test_base_url_creates_provider_override(self):
        config = load_config_from_string(CONFIG, check_api_keys=False)
        openai_d = FakeDelegate("openai")

        delegate = MultiDelegate(
            delegates={"openai": openai_d},
            config=config,
        )

        # local-llm has base_url so MultiDelegate creates a per-provider
        # override for it. The shared openai_d handles non-override providers:
        msgs = [{"role": "user", "content": "hi"}]
        result = await delegate.call("gpt4o", "gpt-4o", msgs)
        assert result.success
        assert len(openai_d.calls) == 1

    @pytest.mark.asyncio
    async def test_from_config_with_no_keys(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        config = load_config_from_string(CONFIG, check_api_keys=False)
        delegate = MultiDelegate.from_config(config)

        # Should use MockDelegate fallback for everything
        msgs = [{"role": "user", "content": "hi"}]
        result = await delegate.call(
            "claude-sonnet", "claude-sonnet-4-6", msgs,
        )
        assert result.success
        assert "[mock:" in result.content
