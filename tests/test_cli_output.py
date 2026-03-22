"""Tests for mobiclaw.cli.output."""
from mobiclaw.cli.output import render


def test_render_json():
    render({"a": 1}, "json")  # no crash


def test_render_table():
    render([{"x": 1, "y": 2}], "table")  # no crash
