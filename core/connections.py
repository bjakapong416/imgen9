"""
What this PC has for the AI steps, checked locally and nothing else.

- Command-line AI tools on PATH (Claude Code, Gemini CLI, Codex CLI) and their version. Signing in is
  the tool's own business: it asks the first time it runs, so nothing here reads its credentials.
- Whether API keys are set as environment variables. Only "set or not" leaves this module, never a
  value.
- Image AIs on the web (Gemini, ChatGPT) are not checked at all: the user pastes prompts there, and
  finding out whether someone is signed in would mean reading browser cookies, which this tool never does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from .projects import find_blender

TOOLS = {
    "claude_code": {"cmd": "claude", "name": "Claude Code",
                    "install": "https://docs.claude.com/en/docs/claude-code/overview"},
    "gemini_cli": {"cmd": "gemini", "name": "Gemini CLI", "install": "https://github.com/google-gemini/gemini-cli"},
    "codex_cli": {"cmd": "codex", "name": "Codex CLI", "install": "https://github.com/openai/codex"},
}
KEYS = {
    "anthropic": {"vars": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "name": "Anthropic (Claude)",
                  "get": "https://console.anthropic.com/settings/keys"},
    "gemini": {"vars": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "name": "Google Gemini",
               "get": "https://aistudio.google.com/apikey"},
    "openai": {"vars": ("OPENAI_API_KEY",), "name": "OpenAI", "get": "https://platform.openai.com/api-keys"},
}
CACHE_S = 300
_cache: dict = {}


def _version(path: str) -> str | None:
    """First line of `<tool> --version`, or None when it doesn't answer quickly."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=6,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or out.stderr or "").strip().splitlines()
    return line[0][:60] if line else None


def status(refresh: bool = False) -> dict:
    if not refresh and _cache.get("at", 0) > time.time() - CACHE_S:
        return _cache["data"]
    tools = {}
    for key, t in TOOLS.items():
        path = shutil.which(t["cmd"])
        tools[key] = {"name": t["name"], "cmd": t["cmd"], "found": bool(path),
                      "version": _version(path) if path else None, "install": t["install"]}
    keys = {k: {"name": v["name"], "set": any(os.getenv(n) for n in v["vars"]), "vars": list(v["vars"]), "get": v["get"]}
            for k, v in KEYS.items()}
    data = {"tools": tools, "keys": keys, "blender": bool(find_blender()), "checked": time.strftime("%H:%M:%S")}
    _cache.update(at=time.time(), data=data)
    return data
