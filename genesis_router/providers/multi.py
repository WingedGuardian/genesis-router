"""Multi-provider delegate — routes calls to the right SDK by provider type.

This is how you use multiple real providers (Anthropic + OpenAI + Groq) with
a single Router instance. The MultiDelegate inspects each provider's type
from the routing config and dispatches to the matching SDK delegate.
"""

from __future__ import annotations

import logging
import os

from genesis_router.types import CallResult, RoutingConfig

logger = logging.getLogger(__name__)


class MultiDelegate:
    """Routes calls to provider-specific delegates based on provider type.

    Usage::

        delegate = MultiDelegate(
            delegates={
                "anthropic": AnthropicDelegate(api_key="sk-..."),
                "openai": OpenAIDelegate(api_key="sk-..."),
                "groq": GroqDelegate(api_key="gsk-..."),
            },
            config=config,
        )
        router = Router(config, breakers, delegate)
    """

    # Provider types that use the OpenAI-compatible API
    _OPENAI_COMPAT = frozenset({
        "lmstudio", "ollama", "qwen", "deepseek", "deepinfra", "together",
    })

    def __init__(
        self,
        delegates: dict,  # provider_type → CallDelegate
        config: RoutingConfig,
        fallback=None,  # CallDelegate | None
    ) -> None:
        self._delegates = delegates
        self._config = config
        self._fallback = fallback
        # Per-provider overrides for providers with custom base_url.
        # These get their own OpenAI client instance with the right endpoint.
        self._provider_overrides: dict = {}
        self._build_overrides()

    def _build_overrides(self) -> None:
        """Create per-provider delegates for OpenAI-compatible providers with base_url."""
        for name, cfg in self._config.providers.items():
            if cfg.base_url and cfg.provider_type in self._OPENAI_COMPAT:
                try:
                    from genesis_router.providers.openai import OpenAIDelegate

                    self._provider_overrides[name] = OpenAIDelegate(
                        base_url=cfg.base_url,
                    )
                except ImportError:
                    pass  # No openai package — will fall through to fallback

    def _resolve_delegate(self, provider: str):
        """Look up the delegate for a provider name via its type in config."""
        # Check per-provider override first (for custom base_url)
        override = self._provider_overrides.get(provider)
        if override is not None:
            return override

        cfg = self._config.providers.get(provider)
        if cfg is None:
            return self._fallback
        ptype = cfg.provider_type
        # Check for direct provider-type match
        delegate = self._delegates.get(ptype)
        if delegate is not None:
            return delegate
        # OpenAI-compatible types fall back to "openai" delegate
        if ptype in self._OPENAI_COMPAT and "openai" in self._delegates:
            return self._delegates["openai"]
        return self._fallback

    async def call(
        self,
        provider: str,
        model_id: str,
        messages: list[dict],
        **kwargs,
    ) -> CallResult:
        delegate = self._resolve_delegate(provider)
        if delegate is None:
            cfg = self._config.providers.get(provider)
            ptype = cfg.provider_type if cfg else "unknown"
            return CallResult(
                success=False,
                error=f"No delegate registered for provider type '{ptype}' "
                f"(provider: {provider})",
                status_code=None,
            )

        return await delegate.call(provider, model_id, messages, **kwargs)

    @classmethod
    def from_config(cls, config: RoutingConfig) -> MultiDelegate:
        """Auto-build delegates by detecting API keys in environment.

        Checks for ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY.
        Providers without detected keys get a MockDelegate fallback.

        This is the zero-config path — just set env vars and go::

            config = load_config("config.yaml")
            delegate = MultiDelegate.from_config(config)
            router = Router(config, breakers, delegate)
        """
        delegates: dict = {}

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                from genesis_router.providers.anthropic import AnthropicDelegate

                delegates["anthropic"] = AnthropicDelegate()
                logger.info("MultiDelegate: Anthropic delegate loaded")
            except ImportError:
                logger.warning(
                    "ANTHROPIC_API_KEY set but anthropic package not installed. "
                    "Install: pip install genesis-router[anthropic]"
                )

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            try:
                from genesis_router.providers.openai import OpenAIDelegate

                delegates["openai"] = OpenAIDelegate()
                logger.info("MultiDelegate: OpenAI delegate loaded")
            except ImportError:
                logger.warning(
                    "OPENAI_API_KEY set but openai package not installed. "
                    "Install: pip install genesis-router[openai]"
                )

        groq_key = os.environ.get("GROQ_API_KEY")
        if groq_key:
            try:
                from genesis_router.providers.groq import GroqDelegate

                delegates["groq"] = GroqDelegate()
                logger.info("MultiDelegate: Groq delegate loaded")
            except ImportError:
                logger.warning(
                    "GROQ_API_KEY set but groq package not installed. "
                    "Install: pip install genesis-router[groq]"
                )

        # MockDelegate as fallback for provider types without an API key
        from genesis_router.providers.mock import MockDelegate

        fallback = MockDelegate()
        if not delegates:
            logger.warning(
                "MultiDelegate: No API keys detected in environment. "
                "All calls will use MockDelegate. "
                "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY."
            )

        return cls(delegates, config, fallback=fallback)
