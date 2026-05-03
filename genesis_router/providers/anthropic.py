"""Anthropic provider — Claude models via the Anthropic SDK.

Install: pip install genesis-router[anthropic]
"""

from __future__ import annotations

import logging

from genesis_router.types import CallResult

logger = logging.getLogger(__name__)


class AnthropicDelegate:
    """CallDelegate implementation using the Anthropic Python SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError:
            msg = "anthropic package required: pip install genesis-router[anthropic]"
            raise ImportError(msg) from None
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def call(
        self,
        provider: str,
        model_id: str,
        messages: list[dict],
        **kwargs,
    ) -> CallResult:
        try:
            system = kwargs.pop("system", None)
            resp = await self._client.messages.create(
                model=model_id,
                max_tokens=kwargs.pop("max_tokens", 1024),
                system=system or "You are a helpful assistant.",
                messages=messages,
                **kwargs,
            )
            return CallResult(
                success=True,
                content=resp.content[0].text if resp.content else "",
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=0.0,  # Caller can compute from model pricing
                cost_known=False,
            )
        except Exception as e:
            status = getattr(e, "status_code", 500)
            return CallResult(
                success=False,
                error=str(e),
                status_code=status,
            )
