"""模块兼容层：实际实现位于 tools/ 目录下。"""

from __future__ import annotations

import importlib
import os

_pkg_dir = os.path.join(os.path.dirname(__file__), "tools")
__path__ = [_pkg_dir]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

mobi = importlib.import_module("seneschal.tools.mobi")
shell = importlib.import_module("seneschal.tools.shell")
file_tools = importlib.import_module("seneschal.tools.file")
web = importlib.import_module("seneschal.tools.web")
papers = importlib.import_module("seneschal.tools.papers")
office = importlib.import_module("seneschal.tools.office")
ocr = importlib.import_module("seneschal.tools.ocr")

call_mobi_action = mobi.call_mobi_action
call_mobi_collect_verified = mobi.call_mobi_collect_verified
run_shell_command = shell.run_shell_command
write_text_file = file_tools.write_text_file
fetch_url_text = web.fetch_url_text
fetch_url_readable_text = web.fetch_url_readable_text
fetch_url_links = web.fetch_url_links
brave_search = web.brave_search
arxiv_search = papers.arxiv_search
dblp_conference_search = papers.dblp_conference_search
download_file = papers.download_file
extract_pdf_text = papers.extract_pdf_text
read_docx_text = office.read_docx_text
create_docx_from_text = office.create_docx_from_text
edit_docx = office.edit_docx
create_pdf_from_text = office.create_pdf_from_text
read_xlsx_summary = office.read_xlsx_summary
write_xlsx_from_records = office.write_xlsx_from_records
write_xlsx_from_rows = office.write_xlsx_from_rows
extract_image_text_ocr = ocr.extract_image_text_ocr

__all__ = [
    "call_mobi_action",
    "call_mobi_collect_verified",
    "run_shell_command",
    "write_text_file",
    "fetch_url_text",
    "fetch_url_readable_text",
    "fetch_url_links",
    "brave_search",
    "arxiv_search",
    "dblp_conference_search",
    "download_file",
    "extract_pdf_text",
    "read_docx_text",
    "create_docx_from_text",
    "edit_docx",
    "create_pdf_from_text",
    "read_xlsx_summary",
    "write_xlsx_from_records",
    "write_xlsx_from_rows",
    "extract_image_text_ocr",
]
