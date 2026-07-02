"""Step D5 · console error 监控测试。"""

from dano.execution.page.console_monitor import (
    ConsoleEntry, filter_errors, summarize_console_logs, is_relevant_error,
)


def _entry(type_, text, url=""):
    return ConsoleEntry(type=type_, text=text, url=url)


def test_to_dict():
    e = _entry("error", "TypeError", "https://x/app.js:1")
    d = e.to_dict()
    assert d["type"] == "error"
    assert d["text"] == "TypeError"
    assert d["url"] == "https://x/app.js:1"


def test_from_dict():
    e = ConsoleEntry.from_dict({"type": "warning", "text": "deprecated", "url": "u", "ts": 1.0})
    assert e.type == "warning" and e.text == "deprecated" and e.ts == 1.0


def test_filter_errors():
    entries = [_entry("log", "x"), _entry("error", "e1"), _entry("warning", "w"), _entry("error", "e2")]
    errs = filter_errors(entries)
    assert len(errs) == 2
    assert all(e.type == "error" for e in errs)


def test_summarize_empty():
    s = summarize_console_logs([])
    assert s == {"total": 0, "errors": 0, "warnings": 0, "sample": ""}


def test_summarize_basic():
    entries = [_entry("log", "x"), _entry("log", "y"), _entry("warning", "w"),
               _entry("error", "e1"), _entry("error", "e2")]
    s = summarize_console_logs(entries)
    assert s["total"] == 5
    assert s["errors"] == 2
    assert s["warnings"] == 1
    assert s["sample"] == "e1"


def test_summarize_truncates_sample():
    entries = [_entry("error", "x" * 500)]
    s = summarize_console_logs(entries, max_sample_len=200)
    assert len(s["sample"]) == 200


def test_is_relevant_blocks_noise():
    assert is_relevant_error("error", "Failed to load favicon.ico 404") is False
    assert is_relevant_error("info", "Download the React DevTools") is False
    assert is_relevant_error("error", "[HMR] update") is False


def test_is_relevant_keeps_real_errors():
    assert is_relevant_error("error", "TypeError: x is undefined") is True
    assert is_relevant_error("error", "ReferenceError: bar is not defined") is True
    assert is_relevant_error("error", "") is False
    assert is_relevant_error("warning", "deprecated") is False