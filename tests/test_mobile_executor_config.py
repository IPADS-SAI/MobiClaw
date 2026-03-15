from __future__ import annotations

from mobiclaw.mobile.config import resolve_provider_config


def test_provider_alias_and_defaults(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    cfg = resolve_provider_config(provider=None)
    assert cfg.name == "mobiagent"
    assert cfg.model == "MobiMind-1.5-4B"


def test_global_env_overrides_provider_specific(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "qwen")
    monkeypatch.setenv("MOBILE_API_BASE", "http://global/v1")
    monkeypatch.setenv("MOBILE_MODEL", "global-model")
    monkeypatch.setenv("MOBILE_TEMPERATURE", "0.7")
    monkeypatch.setenv("MOBILE_QWEN_API_BASE", "http://provider/v1")
    cfg = resolve_provider_config(provider=None)
    assert cfg.api_base == "http://global/v1"
    assert cfg.model == "global-model"
    assert cfg.temperature == 0.7


def test_provider_extras(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "autoglm")
    monkeypatch.setenv("MOBILE_AUTOGLM_MAX_TOKENS", "2048")
    cfg = resolve_provider_config(provider=None)
    assert cfg.extras.get("max_tokens") == 2048


def test_api_base_normalization_for_ip(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.setenv("MOBILE_API_BASE", "166.111.53.96:7003")
    cfg = resolve_provider_config(provider=None)
    assert cfg.api_base == "http://166.111.53.96:7003/v1"


def test_mobiagent_legacy_ports_are_collected(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.delenv("MOBILE_API_BASE", raising=False)
    monkeypatch.delenv("MOBILE_MOBIAGENT_SERVER_IP", raising=False)
    monkeypatch.delenv("MOBILE_MOBIAGENT_DECIDER_PORT", raising=False)
    monkeypatch.delenv("MOBILE_MOBIAGENT_GROUNDER_PORT", raising=False)
    monkeypatch.delenv("MOBILE_MOBIAGENT_PLANNER_PORT", raising=False)
    monkeypatch.setenv("MOBIAGENT_SERVER_IP", "166.111.53.96")
    monkeypatch.setenv("MOBIAGENT_SERVER_DECIDER_PORT", "7003")
    monkeypatch.setenv("MOBIAGENT_SERVER_GROUNDER_PORT", "7004")
    monkeypatch.setenv("MOBIAGENT_SERVER_PLANNER_PORT", "7002")
    cfg = resolve_provider_config(provider=None)
    assert cfg.api_base == ""
    assert cfg.extras.get("service_ip") == "166.111.53.96"
    assert cfg.extras.get("decider_port") == 7003
    assert cfg.extras.get("grounder_port") == 7004
    assert cfg.extras.get("planner_port") == 7002


def test_mobiagent_prefixed_server_ip_maps_to_service_ip(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.delenv("MOBILE_API_BASE", raising=False)
    monkeypatch.setenv("MOBILE_MOBIAGENT_SERVER_IP", "166.111.53.96")
    monkeypatch.setenv("MOBILE_MOBIAGENT_DECIDER_PORT", "7003")
    monkeypatch.setenv("MOBILE_MOBIAGENT_GROUNDER_PORT", "7004")
    monkeypatch.setenv("MOBILE_MOBIAGENT_PLANNER_PORT", "7002")

    cfg = resolve_provider_config(provider=None)
    assert cfg.api_base == ""
    assert cfg.extras.get("service_ip") == "166.111.53.96"
    assert cfg.extras.get("decider_port") == 7003
    assert cfg.extras.get("grounder_port") == 7004
    assert cfg.extras.get("planner_port") == 7002


def test_mobiagent_explicit_api_base_overrides_split_ports(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.setenv("MOBILE_MOBIAGENT_API_BASE", "http://166.111.53.96:7003/v1")
    monkeypatch.setenv("MOBILE_MOBIAGENT_SERVER_IP", "166.111.53.96")
    monkeypatch.setenv("MOBILE_MOBIAGENT_DECIDER_PORT", "7003")
    monkeypatch.setenv("MOBILE_MOBIAGENT_GROUNDER_PORT", "7004")
    monkeypatch.setenv("MOBILE_MOBIAGENT_PLANNER_PORT", "7002")

    cfg = resolve_provider_config(provider=None)
    assert cfg.api_base == "http://166.111.53.96:7003/v1"


def test_global_max_retries_is_applied(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "qwen")
    monkeypatch.setenv("MOBILE_MAX_RETRIES", "4")
    cfg = resolve_provider_config(provider=None)
    assert cfg.extras.get("max_retries") == 4


def test_provider_max_retries_overrides_global(monkeypatch):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.setenv("MOBILE_MAX_RETRIES", "2")
    monkeypatch.setenv("MOBILE_MOBIAGENT_MAX_RETRIES", "5")
    cfg = resolve_provider_config(provider=None)
    assert cfg.extras.get("max_retries") == 5
