"""Groq provider — fast inference via the Groq SDK.

Install: pip install genesis-router[groq]
"""

from __future__ import annotations

import logging

from genesis_router.types import CallResult

logger = logging.getLogger(__name__)


class GroqDelegate:
    """CallDelegate implementation using the Groq Python SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        try:
            from groq import AsyncGroq
        except ImportError:
            msg = "groq package required: pip install genesis-router[groq]"
            raise ImportError(msg) from None
        self._client = AsyncGroq(api_key=api_key)

    async def call(
        self,
        provider: str,
        model_id: str,
        messages: list[dict],
        **kwargs,
    ) -> CallResult:
        try:
            system = kwargs.pop("system", None)
            all_msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = await self._client.chat.completions.create(
                model=model_id,
                max_tokens=kwargs.pop("max_tokens", 1024),
                messages=all_msgs,
                **kwargs,
            )
            choice = resp.choices[0]
            return CallResult(
                success=True,
                content=choice.message.content or "",
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
                cost_usd=0.0,
                cost_known=False,
            )
        except Exception as e:
            status = getattr(e, "status_code", 500)
            return CallResult(
                success=False,
                error=str(e),
                status_code=status,
            )
