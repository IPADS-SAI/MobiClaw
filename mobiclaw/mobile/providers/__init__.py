# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Provider registry for unified mobile executor (lazy import)."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_PROVIDER_CLASS_PATHS = {
    "mobiagent": "mobiclaw.mobile.providers.mobiagent.mobile_task:MobiAgentStepTask",
    "uitars": "mobiclaw.mobile.providers.uitars.uitars_task:UITARSTask",
    "qwen": "mobiclaw.mobile.providers.qwen.qwen_task:QwenTask",
    "autoglm": "mobiclaw.mobile.providers.autoglm.autoglm_task:AutoGLMTask",
}

PROVIDER_REGISTRY = dict(_PROVIDER_CLASS_PATHS)


def get_provider_class(name: str) -> Any:
    spec = _PROVIDER_CLASS_PATHS.get(name)
    if not spec:
        return None
    module_name, class_name = spec.split(":")
    module = import_module(module_name)
    return getattr(module, class_name, None)


__all__ = ["PROVIDER_REGISTRY", "get_provider_class"]

