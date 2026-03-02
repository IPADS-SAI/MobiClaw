"""模块兼容层：实际实现位于 tools/ 目录下。"""

from __future__ import annotations

import importlib
import os

_pkg_dir = os.path.join(os.path.dirname(__file__), "tools")
__path__ = [_pkg_dir]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

mobi = importlib.import_module("seneschal.tools.mobi")
weknora = importlib.import_module("seneschal.tools.weknora")
shell = importlib.import_module("seneschal.tools.shell")
file_tools = importlib.import_module("seneschal.tools.file")
web = importlib.import_module("seneschal.tools.web")

call_mobi_action = mobi.call_mobi_action
call_mobi_collect = mobi.call_mobi_collect
weknora_add_knowledge = weknora.weknora_add_knowledge
weknora_knowledge_search = weknora.weknora_knowledge_search
weknora_list_knowledge_bases = weknora.weknora_list_knowledge_bases
weknora_rag_chat = weknora.weknora_rag_chat
run_shell_command = shell.run_shell_command
write_text_file = file_tools.write_text_file
fetch_url_text = web.fetch_url_text
fetch_url_readable_text = web.fetch_url_readable_text
fetch_url_links = web.fetch_url_links

__all__ = [
    "call_mobi_action",
    "call_mobi_collect",
    "weknora_add_knowledge",
    "weknora_knowledge_search",
    "weknora_list_knowledge_bases",
    "weknora_rag_chat",
    "run_shell_command",
    "write_text_file",
    "fetch_url_text",
    "fetch_url_readable_text",
    "fetch_url_links",
]
