"""Model alias resolution — short names to full model identifiers.

Provides fuzzy matching so users can type "sonnet" instead of
"anthropic/claude-sonnet-4-6" or "haiku" instead of the full ID.
"""

from __future__ import annotations

import difflib

# Short name -> full model identifier.
# Keep sorted by short name for readability.
MODEL_ALIASES: dict[str, str] = {
    "claude": "anthropic/claude-sonnet-4-6",
    "deepseek": "deepseek/deepseek-chat",
    "flash": "google/gemini-2.5-flash",
    "gemini": "google/gemini-2.5-flash",
    "gpt4": "openai/gpt-4o",
    "gpt4mini": "openai/gpt-4o-mini",
    "gpt4o": "openai/gpt-4o",
    "haiku": "anthropic/claude-haiku-4-5",
    "llama": "meta-llama/llama-4-scout",
    "o1": "openai/o1",
    "o3": "openai/o3-mini",
    "opus": "anthropic/claude-opus-4-6",
    "r1": "deepseek/deepseek-r1",
    "sonnet": "anthropic/claude-sonnet-4-6",
}

_NORMALIZED: dict[str, str] = {}


def _normalize(s: str) -> str:
    """Strip hyphens, underscores, spaces, dots and lowercase."""
    return s.lower().replace("-", "").replace("_", "").replace(" ", "").replace(".", "")


def _build_normalized() -> None:
    _NORMALIZED.clear()
    for key in MODEL_ALIASES:
        _NORMALIZED[_normalize(key)] = key


_build_normalized()


def resolve_model(raw: str) -> tuple[str | None, str | None]:
    """Resolve a user-typed model name to a full identifier.

    Returns (full_model_id, matched_alias_key) or (None, suggestion_message).
    If raw contains '/', it's treated as a full model ID and passed through.
    """
    if not raw:
        return None, None

    # Full model ID — pass through
    if "/" in raw:
        return raw, raw

    lowered = raw.lower()

    # 1. Exact match
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered], lowered

    # 2. Normalized match
    normed = _normalize(raw)
    if normed in _NORMALIZED:
        key = _NORMALIZED[normed]
        return MODEL_ALIASES[key], key

    # 3. Fuzzy match
    candidates = difflib.get_close_matches(normed, _NORMALIZED.keys(), n=2, cutoff=0.7)
    if len(candidates) == 1:
        key = _NORMALIZED[candidates[0]]
        return MODEL_ALIASES[key], key

    if len(candidates) > 1:
        suggestions = [_NORMALIZED[c] for c in candidates]
        return None, f"Ambiguous model '{raw}'. Did you mean: {', '.join(suggestions)}?"

    # 4. No match
    valid = ", ".join(sorted(MODEL_ALIASES.keys()))
    return None, f"Unknown model '{raw}'. Short names: {valid}"
