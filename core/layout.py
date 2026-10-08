"""
Where things live on disk. Characters are grouped into projects (a game or a theme):

    projects/<project>/group.json            name + settings shared by its characters
    projects/<project>/style.png             optional style reference image
    projects/<project>/<character>/          one character (project.json, concept, views, sheets, ...)
    renders/<project>/<character>/sprite/    its built sprite (.spr/.act, build.json, game.json)

Character ids ("slugs") stay unique across all projects, so URLs and API paths only need the
character id (/projects/<character>/..., /api/projects/<character>); this module finds its project.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

DEFAULT_GROUP = "my_characters"
GROUP_RE = re.compile(r"^[a-z0-9_]{1,48}$")
# Shared settings a project can set for all its characters (each character can still override).
GROUP_KEYS = ("name", "style_en", "body", "detail")


class LayoutError(ValueError):
    pass


def group_slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:40]
    return s or f"project_{int(time.time())}"


class Layout:
    def __init__(self, projects_dir: Path, renders_dir: Path):
        self.projects_dir = projects_dir
        self.renders_dir = renders_dir
        self._where: dict[str, str] = {}      # character -> project, rebuilt on a miss
        self._lock = threading.Lock()

    # ───────── projects (groups) ─────────
    def group_dir(self, gid: str) -> Path:
        if not GROUP_RE.match(gid or ""):
            raise LayoutError("Invalid project id.")
        return self.projects_dir / gid

    def groups(self) -> list[str]:
        if not self.projects_dir.is_dir():
            return []
        return sorted(d.name for d in self.projects_dir.iterdir()
                      if d.is_dir() and GROUP_RE.match(d.name) and (d / "group.json").exists())

    def read_group(self, gid: str) -> dict:
        f = self.group_dir(gid) / "group.json"
        if not f.exists():
            raise LayoutError(f"No project '{gid}'.")
        g = json.loads(f.read_text(encoding="utf-8"))
        g["id"] = gid
        return g

    def write_group(self, gid: str, data: dict) -> dict:
        d = self.group_dir(gid)
        d.mkdir(parents=True, exist_ok=True)
        keep = {k: data.get(k) for k in GROUP_KEYS if data.get(k) not in (None, "")}
        keep["created"] = data.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S")
        (d / "group.json").write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.read_group(gid)

    def ensure_group(self, gid: str, name: str = "") -> dict:
        if not (self.group_dir(gid) / "group.json").exists():
            return self.write_group(gid, {"name": name})
        return self.read_group(gid)

    def new_group(self, name: str) -> dict:
        base = gid = group_slug(name)
        n = 2
        while self.group_dir(gid).exists():
            gid = f"{base}_{n}"
            n += 1
        return self.write_group(gid, {"name": name.strip()[:80]})

    def characters(self, gid: str) -> list[str]:
        d = self.group_dir(gid)
        return sorted(c.name for c in d.iterdir() if c.is_dir() and (c / "project.json").exists()) if d.is_dir() else []

    # ───────── characters ─────────
    def _scan(self) -> dict[str, str]:
        where: dict[str, str] = {}
        for base in (self.renders_dir, self.projects_dir):      # a project folder wins over a render-only one
            if base.is_dir():
                for g in base.iterdir():
                    if g.is_dir() and GROUP_RE.match(g.name):
                        for c in g.iterdir():
                            if c.is_dir():
                                where[c.name] = g.name
        return where

    def group_of(self, slug: str) -> str | None:
        with self._lock:
            if slug not in self._where:
                self._where = self._scan()
            return self._where.get(slug)

    def forget(self) -> None:
        with self._lock:
            self._where = {}

    def exists(self, slug: str) -> bool:
        return self.group_of(slug) is not None

    def project_dir(self, slug: str) -> Path:
        gid = self.group_of(slug)
        if gid is None:
            raise LayoutError(f"No character '{slug}'.")
        return self.projects_dir / gid / slug

    def render_dir(self, slug: str, gid: str | None = None) -> Path:
        """renders/<project>/<character>/ (it may not exist yet)."""
        gid = gid or self.group_of(slug) or DEFAULT_GROUP
        return self.renders_dir / gid / slug

    def sprite_rel(self, slug: str, rest: str) -> str:
        """Path under renders/ that sprite ids are made from: "<project>/<character>/<rest>"."""
        return f"{self.group_of(slug) or DEFAULT_GROUP}/{slug}/{rest}"

    def move(self, slug: str, gid: str) -> None:
        """Move a character (its project folder and its renders) into another project."""
        old = self.group_of(slug)
        if old is None:
            raise LayoutError(f"No character '{slug}'.")
        self.read_group(gid)                          # must exist
        if old == gid:
            return
        for base in (self.projects_dir, self.renders_dir):
            src = base / old / slug
            if src.exists():
                (base / gid).mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(base / gid / slug))
        self.forget()
