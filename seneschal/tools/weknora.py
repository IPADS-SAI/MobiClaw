"""模块加载器：实际实现位于 weknora/ 目录。"""

from __future__ import annotations

import importlib
import os

_pkg_dir = os.path.join(os.path.dirname(__file__), "weknora")
__path__ = [_pkg_dir]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

_pkg = importlib.import_module("seneschal.tools.weknora")

globals().update({name: getattr(_pkg, name) for name in getattr(_pkg, "__all__", [])})
