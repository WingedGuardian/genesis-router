"""Provider implementations for the CallDelegate protocol."""

from genesis_router.providers.mock import MockDelegate
from genesis_router.providers.multi import MultiDelegate

__all__ = ["MockDelegate", "MultiDelegate"]

# Concrete providers are imported on demand to avoid hard SDK dependencies:
#   from genesis_router.providers.anthropic import AnthropicDelegate
#   from genesis_router.providers.openai import OpenAIDelegate
#   from genesis_router.providers.groq import GroqDelegate
