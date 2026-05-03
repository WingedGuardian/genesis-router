"""Config loader — YAML to RoutingConfig with env var expansion and local overlays."""

from __future__ import annotations

import copy
import logging
import os
import re
from pathlib import Path

import yaml

from genesis_router.protocols import ApiKeyChecker, default_api_key_checker
from genesis_router.types import (
    CallSiteConfig,
    ProviderConfig,
    RetryPolicy,
    RoutingConfig,
)

logger = logging.getLogger(__name__)
_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")

# Providers that don't need API keys (local inference)
_LOCAL_PROVIDER_TYPES = frozenset({"ollama", "lmstudio"})


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base. Lists are replaced, not appended."""
    merged = copy.deepcopy(base)
    for key, val in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _local_path_for(path: Path) -> Path:
    """Derive the .local.yaml path for a base config file."""
    return path.with_name(f"{path.stem}.local.yaml")


def _load_local_overlay(path: Path) -> dict:
    """Load the .local.yaml overlay for a config path. Returns {} if none."""
    local = _local_path_for(path)
    if not local.is_file():
        return {}
    try:
        return yaml.safe_load(local.read_text()) or {}
    except Exception:
        logger.warning("Failed to read local overlay %s", local, exc_info=True)
        return {}


def _sanitize_local_overlay(base_raw: dict, local_raw: dict) -> dict:
    """Filter stale provider references from a local overlay before merging."""
    result = copy.deepcopy(local_raw)
    base_providers = set((base_raw.get("providers") or {}).keys())
    local_call_sites = result.get("call_sites") or {}

    for cs_name, cs in list(local_call_sites.items()):
        if not isinstance(cs, dict) or "chain" not in cs:
            continue
        original_chain = cs["chain"]
        filtered = [p for p in original_chain if p in base_providers]
        stale = set(original_chain) - set(filtered)
        if stale:
            logger.warning(
                "Local override for call site '%s' references unknown "
                "provider(s) %s — skipping them",
                cs_name,
                sorted(stale),
            )
        if not filtered:
            logger.warning(
                "Local override for call site '%s' has no valid providers "
                "after filtering — dropping local chain override",
                cs_name,
            )
            del cs["chain"]
            if not cs:
                del local_call_sites[cs_name]
        else:
            cs["chain"] = filtered

    return result


def load_config(
    path: str | Path,
    *,
    check_api_keys: bool = True,
    api_key_checker: ApiKeyChecker | None = None,
) -> RoutingConfig:
    """Load routing config from a YAML file.

    Checks for a ``{stem}.local.yaml`` overlay in the same directory and
    deep-merges it on top of the base config before parsing.

    Args:
        path: Path to the YAML config file.
        check_api_keys: If True, auto-disable providers without API keys.
        api_key_checker: Callable that returns True if a provider_type has
            a configured API key. Defaults to accepting all providers.
    """
    path = Path(path)
    text = path.read_text()
    base_raw = yaml.safe_load(_expand_env_vars(text))

    local_raw = _load_local_overlay(path)
    if local_raw:
        local_raw = _sanitize_local_overlay(base_raw, local_raw)
        if local_raw:
            base_raw = _deep_merge(base_raw, local_raw)

    return _parse(base_raw, check_api_keys=check_api_keys, api_key_checker=api_key_checker)


def load_config_from_string(
    text: str,
    *,
    check_api_keys: bool = True,
    api_key_checker: ApiKeyChecker | None = None,
) -> RoutingConfig:
    """Load routing config from a YAML string (no overlay support)."""
    raw = yaml.safe_load(_expand_env_vars(text))
    return _parse(raw, check_api_keys=check_api_keys, api_key_checker=api_key_checker)


def _expand_env_vars(text: str) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders in config text."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        return os.environ.get(key, default if default is not None else match.group(0))

    return _ENV_PATTERN.sub(repl, text)


def _parse(
    raw: dict,
    *,
    check_api_keys: bool = True,
    api_key_checker: ApiKeyChecker | None = None,
) -> RoutingConfig:
    """Parse raw YAML dict into a validated RoutingConfig."""
    if not isinstance(raw, dict):
        msg = "Config must be a YAML mapping"
        raise ValueError(msg)

    checker = api_key_checker or default_api_key_checker

    # --- Retry profiles ---
    retry_profiles: dict[str, RetryPolicy] = {}
    for name, rp in (raw.get("retry") or {}).items():
        retry_profiles[name] = RetryPolicy(
            max_retries=rp.get("max_retries", 3),
            base_delay_ms=rp.get("base_delay_ms", 500),
            max_delay_ms=rp.get("max_delay_ms", 30000),
            backoff_multiplier=rp.get("backoff_multiplier", 2.0),
            jitter_pct=rp.get("jitter_pct", 0.25),
        )
    if "default" not in retry_profiles:
        retry_profiles["default"] = RetryPolicy()

    # --- Providers ---
    providers: dict[str, ProviderConfig] = {}
    disabled_providers: set[str] = set()
    disabled_provider_types: dict[str, str] = {}

    for name, p in (raw.get("providers") or {}).items():
        # Parse enabled field — supports bool, string from env var expansion
        enabled_raw = p.get("enabled", True)
        if isinstance(enabled_raw, str):
            enabled = enabled_raw.strip().lower() not in {"0", "false", "no", "off", ""}
        else:
            enabled = bool(enabled_raw)

        if not enabled:
            disabled_providers.add(name)
            disabled_provider_types[name] = p.get("type", "unknown")
            logger.info("Provider '%s' disabled via config", name)
            continue

        cfg = ProviderConfig(
            name=name,
            provider_type=p["type"],
            model_id=p["model"],
            is_free=p.get("free", False),
            rpm_limit=p.get("rpm_limit"),
            open_duration_s=p.get("open_duration_s", 120),
            base_url=p.get("base_url"),
            keep_alive=p.get("keep_alive"),
            enabled=True,
            profile=p.get("profile"),
        )

        # Auto-disable providers without API keys (skip local providers)
        if (
            check_api_keys
            and cfg.provider_type not in _LOCAL_PROVIDER_TYPES
            and not checker(cfg.provider_type)
        ):
            disabled_providers.add(name)
            disabled_provider_types[name] = cfg.provider_type
            logger.info("Provider '%s' disabled: no API key configured", name)
            continue

        providers[name] = cfg

    # --- Call sites ---
    call_sites: dict[str, CallSiteConfig] = {}
    for name, cs in (raw.get("call_sites") or {}).items():
        chain = cs["chain"]
        chain = [p for p in chain if p not in disabled_providers]

        if not chain:
            logger.warning(
                "Call site '%s' has no enabled providers — skipping",
                name,
            )
            continue

        for provider in chain:
            if provider not in providers:
                msg = f"Call site '{name}' references unknown provider '{provider}'"
                raise ValueError(msg)

        retry_profile = cs.get("retry_profile", "default")
        if retry_profile not in retry_profiles:
            msg = f"Call site '{name}' references unknown retry profile '{retry_profile}'"
            raise ValueError(msg)

        call_sites[name] = CallSiteConfig(
            id=name,
            chain=tuple(chain),
            default_paid=cs.get("default_paid", False),
            never_pays=cs.get("never_pays", False),
            retry_profile=retry_profile,
        )

    return RoutingConfig(
        providers=providers,
        call_sites=call_sites,
        retry_profiles=retry_profiles,
        disabled_providers=disabled_provider_types,
    )
