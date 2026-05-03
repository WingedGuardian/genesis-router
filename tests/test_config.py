"""Tests for YAML config loading."""

import textwrap

import pytest

from genesis_router.config import load_config_from_string


class TestLoadConfig:
    def test_basic_config(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: test-model
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert "test" in config.providers
        assert "chat" in config.call_sites
        assert config.call_sites["chat"].chain == ("test",)

    def test_retry_profiles(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: m
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
            retry_profile: fast
        retry:
          fast:
            max_retries: 1
            base_delay_ms: 100
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert "fast" in config.retry_profiles
        assert config.retry_profiles["fast"].max_retries == 1
        assert "default" in config.retry_profiles  # always created

    def test_default_retry_profile_created(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: m
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert "default" in config.retry_profiles

    def test_env_var_expansion(self, monkeypatch):
        monkeypatch.setenv("TEST_MODEL", "gpt-4o")
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: openai
            model: ${TEST_MODEL}
            free: false
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert config.providers["test"].model_id == "gpt-4o"

    def test_env_var_default(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: ${NONEXISTENT_VAR:-fallback-model}
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert config.providers["test"].model_id == "fallback-model"

    def test_disabled_provider_removed_from_chain(self):
        yaml = textwrap.dedent("""\
        providers:
          active:
            type: mock
            model: m
            free: true
            open_duration_s: 60
          disabled:
            type: mock
            model: m
            free: true
            open_duration_s: 60
            enabled: false
        call_sites:
          chat:
            chain: [active, disabled]
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert "disabled" not in config.providers
        assert config.call_sites["chat"].chain == ("active",)

    def test_never_pays(self):
        yaml = textwrap.dedent("""\
        providers:
          paid:
            type: anthropic
            model: claude
            free: false
            open_duration_s: 60
          free:
            type: groq
            model: llama
            free: true
            open_duration_s: 60
        call_sites:
          cheap:
            chain: [paid, free]
            never_pays: true
        """)
        config = load_config_from_string(yaml, check_api_keys=False)
        assert config.call_sites["cheap"].never_pays

    def test_unknown_provider_in_chain_raises(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: m
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test, nonexistent]
        """)
        with pytest.raises(ValueError, match="unknown provider"):
            load_config_from_string(yaml, check_api_keys=False)

    def test_unknown_retry_profile_raises(self):
        yaml = textwrap.dedent("""\
        providers:
          test:
            type: mock
            model: m
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [test]
            retry_profile: nonexistent
        """)
        with pytest.raises(ValueError, match="unknown retry profile"):
            load_config_from_string(yaml, check_api_keys=False)

    def test_api_key_checker(self):
        yaml = textwrap.dedent("""\
        providers:
          has_key:
            type: anthropic
            model: claude
            free: false
            open_duration_s: 60
          no_key:
            type: openai
            model: gpt
            free: false
            open_duration_s: 60
        call_sites:
          chat:
            chain: [has_key, no_key]
        """)
        # Only anthropic has a key
        config = load_config_from_string(
            yaml,
            api_key_checker=lambda t: t == "anthropic",
        )
        assert "has_key" in config.providers
        assert "no_key" not in config.providers
        assert "openai" in config.disabled_providers.values()

    def test_local_providers_skip_key_check(self):
        yaml = textwrap.dedent("""\
        providers:
          local:
            type: ollama
            model: llama
            free: true
            open_duration_s: 60
        call_sites:
          chat:
            chain: [local]
        """)
        config = load_config_from_string(
            yaml,
            api_key_checker=lambda t: False,  # reject all
        )
        assert "local" in config.providers  # ollama skips key check
