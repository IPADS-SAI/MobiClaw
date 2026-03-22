"""Rich output formatting."""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table


def print_table(columns: list[str], rows: list[list[Any]], title: str | None = None) -> None:
    t = Table(title=title)
    for c in columns:
        t.add_column(c)
    for row in rows:
        t.add_row(*[str(x) for x in row])
    Console().print(t)


def print_json(data: Any) -> None:
    Console().print(Syntax(json.dumps(data, indent=2, ensure_ascii=False), "json"))


def print_text(text: str) -> None:
    Console().print(text)


def render(data: Any, fmt: str) -> None:
    if fmt == "json":
        print_json(data)
    elif fmt == "table":
        if isinstance(data, list) and data and isinstance(data[0], dict):
            cols = list(data[0].keys())
            rows = [[r.get(c) for c in cols] for r in data]
            print_table(cols, rows)
        else:
            print_json(data)
    else:
        print_text(str(data))
