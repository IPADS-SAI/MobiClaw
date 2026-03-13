from __future__ import annotations

import os
import re
from typing import Mapping

from .types import ProviderConfig


_PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "mobiagent": {
        "api_base": "",
        "model": "MobiMind-1.5-4B",
        "temperature": 0.1,
    },
    "uitars": {
        "api_base": "http://localhost:8000/v1",
        "model": "UI-TARS-1.5-7B",
        "temperature": 0.0,
    },
    "qwen": {
        "api_base": "http://localhost:8080/v1",
        "model": "Qwen3-VL-30B-A3B-Instruct",
        "temperature": 0.0,
    },
    "autoglm": {
        "api_base": "http://localhost:8000/v1",
        "model": "autoglm-phone-9b",
        "temperature": 0.0,
    },
}

_PROVIDER_ALIASES = {
    "mobiagent": "mobiagent",
    "mobiagent": "mobiagent",
}

_PROVIDER_PREFIX = {
    "mobiagent": "MOBILE_MOBIAGENT_",
    "uitars": "MOBILE_UITARS_",
    "qwen": "MOBILE_QWEN_",
    "autoglm": "MOBILE_AUTOGLM_",
}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip() not in {"", "0", "false", "False", "no", "NO"}


def _normalize_api_base(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        base = value.rstrip("/")
    elif re.match(r"^[a-zA-Z0-9.-]+(?::\d+)?$", value):
        base = f"http://{value}".rstrip("/")
    else:
        return value

    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _provider_key(name: str) -> str:
    return _PROVIDER_ALIASES.get(name.strip().lower(), name.strip().lower())


def _normalize_extras(provider_name: str, extras: dict[str, str]) -> dict[str, object]:
    out: dict[str, object] = dict(extras)
    if provider_name == "mobiagent":
        if "enable_planning" in out:
            out["enable_planning"] = _as_bool(str(out["enable_planning"]), default=True)
        if "use_e2e" in out:
            out["use_e2e"] = _as_bool(str(out["use_e2e"]), default=True)
        if "use_experience" in out:
            out["use_experience"] = _as_bool(str(out["use_experience"]), default=False)
        for k in ("decider_port", "grounder_port", "planner_port"):
            if k in out and str(out[k]).strip():
                out[k] = int(str(out[k]).strip())
    elif provider_name == "uitars":
        if "step_delay" in out and str(out["step_delay"]).strip():
            out["step_delay"] = float(str(out["step_delay"]).strip())
    elif provider_name == "autoglm":
        for k in ("max_tokens",):
            if k in out and str(out[k]).strip():
                out[k] = int(str(out[k]).strip())
        for k in ("top_p", "frequency_penalty"):
            if k in out and str(out[k]).strip():
                out[k] = float(str(out[k]).strip())
    return out


def _set_default_from_aliases(target: dict[str, str], canonical_key: str, alias_keys: tuple[str, ...]) -> None:
    current = str(target.get(canonical_key, "")).strip()
    if current:
        return
    for alias in alias_keys:
        value = str(target.get(alias, "")).strip()
        if value:
            target[canonical_key] = value
            return


def _canonicalize_mobiagent_extras(extras: dict[str, str]) -> dict[str, str]:
    out = dict(extras)
    _set_default_from_aliases(out, "service_ip", ("server_ip", "ip", "host"))
    _set_default_from_aliases(out, "decider_port", ("server_decider_port",))
    _set_default_from_aliases(out, "grounder_port", ("server_grounder_port",))
    _set_default_from_aliases(out, "planner_port", ("server_planner_port",))
    return out


def resolve_provider_config(provider: str | None, environ: Mapping[str, str] | None = None) -> ProviderConfig:
    env = os.environ if environ is None else environ

    provider_raw = (
        provider
        or env.get("MOBILE_PROVIDER")
        or env.get("MOBIAGENT_PROVIDER")
        or "mobiagent"
    )
    provider_name = _provider_key(provider_raw)
    if provider_name not in _PROVIDER_DEFAULTS:
        raise ValueError(f"Unknown mobile provider: {provider_name}")

    defaults = _PROVIDER_DEFAULTS[provider_name]
    prefix = _PROVIDER_PREFIX.get(provider_name, "")

    extras: dict[str, str] = {}
    if prefix:
        for key, value in env.items():
            if key.startswith(prefix):
                extras[key[len(prefix):].lower()] = value

    # Legacy mobiagent compatibility
    if provider_name == "mobiagent":
        service_ip = (env.get("MOBIAGENT_SERVER_IP") or "").strip()
        decider_port = (env.get("MOBIAGENT_SERVER_DECIDER_PORT") or "").strip()
        grounder_port = (env.get("MOBIAGENT_SERVER_GROUNDER_PORT") or "").strip()
        planner_port = (env.get("MOBIAGENT_SERVER_PLANNER_PORT") or "").strip()
        if service_ip:
            extras.setdefault("service_ip", service_ip)
        if decider_port:
            extras.setdefault("decider_port", decider_port)
        if grounder_port:
            extras.setdefault("grounder_port", grounder_port)
        if planner_port:
            extras.setdefault("planner_port", planner_port)

        # Compatibility with plain env names used by local mobiagent scripts.
        plain_service_ip = (env.get("SERVER_IP") or "").strip()
        plain_decider_port = (env.get("DECIDER_PORT") or "").strip()
        plain_grounder_port = (env.get("GROUNDER_PORT") or "").strip()
        plain_planner_port = (env.get("PLANNER_PORT") or "").strip()
        if plain_service_ip:
            extras.setdefault("service_ip", plain_service_ip)
        if plain_decider_port:
            extras.setdefault("decider_port", plain_decider_port)
        if plain_grounder_port:
            extras.setdefault("grounder_port", plain_grounder_port)
        if plain_planner_port:
            extras.setdefault("planner_port", plain_planner_port)

        extras = _canonicalize_mobiagent_extras(extras)

    global_api_base = (env.get("MOBILE_API_BASE") or "").strip()
    provider_api_base = (extras.get("api_base") or "").strip()
    legacy_api_base = (env.get("MOBIAGENT_BASE_URL") or env.get("MOBI_AGENT_BASE_URL") or "").strip()

    api_base = global_api_base or provider_api_base or legacy_api_base or str(defaults["api_base"])

    global_api_key = (env.get("MOBILE_API_KEY") or "").strip()
    provider_api_key = (extras.get("api_key") or "").strip()
    legacy_api_key = (env.get("MOBIAGENT_API_KEY") or env.get("MOBI_AGENT_API_KEY") or "").strip()
    api_key = global_api_key or provider_api_key or legacy_api_key

    global_model = (env.get("MOBILE_MODEL") or "").strip()
    provider_model = (extras.get("model") or "").strip()
    model = global_model or provider_model or str(defaults["model"])

    global_temp = (env.get("MOBILE_TEMPERATURE") or "").strip()
    provider_temp = (extras.get("temperature") or "").strip()
    if global_temp:
        temperature = float(global_temp)
    elif provider_temp:
        temperature = float(provider_temp)
    else:
        temperature = float(defaults["temperature"])

    max_steps = max(1, int((env.get("MOBILE_MAX_STEPS") or "40").strip() or "40"))
    draw = _as_bool(env.get("MOBILE_DRAW"), default=False)

    return ProviderConfig(
        name=provider_name,
        api_base=_normalize_api_base(api_base),
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_steps=max_steps,
        draw=draw,
        extras=_normalize_extras(provider_name, extras),
    )


def resolve_device_config(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    device_type = (env.get("MOBILE_DEVICE_TYPE") or env.get("DEVICE") or "mock").strip() or "mock"
    device_id = (env.get("MOBILE_DEVICE_ID") or "").strip()
    return device_type, device_id
