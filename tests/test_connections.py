"""The connections check reports what is installed / set, and never a key's value."""

from __future__ import annotations

import json

from core import connections


def test_keys_report_set_but_never_the_value(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(connections.shutil, "which", lambda cmd: None)
    s = connections.status(refresh=True)
    assert s["keys"]["gemini"]["set"] is True
    assert s["keys"]["openai"]["set"] is False
    assert "secret-value-123" not in json.dumps(s)


def test_tools_found_on_path(monkeypatch):
    monkeypatch.setattr(connections.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "claude" else None)
    monkeypatch.setattr(connections, "_version", lambda path: "1.0.0 (Claude Code)")
    s = connections.status(refresh=True)
    assert s["tools"]["claude_code"] == {**s["tools"]["claude_code"], "found": True, "version": "1.0.0 (Claude Code)"}
    assert s["tools"]["gemini_cli"]["found"] is False
    connections._cache.clear()
