"""
Rendered characters (renders/<project>/<name>/) served in the same "animset" format as .spr/.act sprites,
plus the per-character Playtest settings (lab_config.json: move speed, hit frames, ...).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .sprite_packer import needs_pack, pack

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
ORDER_HINT = ["idle", "walk", "run", "attack"]


class CharacterError(ValueError):
    pass


class CharacterLibrary:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def _dir(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise CharacterError("Invalid character name.")
        hits = [d for d in self.root.glob(f"*/{name}") if d.is_dir()] if self.root.is_dir() else []
        if not hits:
            raise CharacterError(f"No rendered character '{name}'.")
        return hits[0]

    def _all(self) -> list[Path]:
        """renders/<project>/<character>/ folders."""
        if not self.root.is_dir():
            return []
        return [d for g in self.root.iterdir() if g.is_dir() for d in g.iterdir() if d.is_dir() and NAME_RE.match(d.name)]

    def _load(self, d: Path) -> dict:
        with self._lock:  # don't pack the same folder twice in parallel
            if needs_pack(d):
                pack(d)
        path = d / "character.json"
        if not path.exists():
            raise CharacterError(f"{d.name} has no character.json / render_manifest.json.")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self._all(), key=lambda p: p.name):
            report = d / "sprite" / "build.json"
            if not (d / "character.json").exists() and not (d / "render_manifest.json").exists() and report.exists():
                # 2D route: the sprite assembled from the AI-drawn action sheets
                from .sprite_library import own_sprite_id
                built = json.loads(report.read_text(encoding="utf-8"))
                out.append({"name": d.name, "folder": d.name, "group": d.parent.name, "source": "2d",
                            "sprite_id": own_sprite_id(f"{d.parent.name}/{d.name}/sprite/{d.name}.act"),
                            "animations": [a["action"] for a in built.get("action_map", [])],
                            "weapon": built.get("weapon"),
                            "body_sprite_id": (own_sprite_id(f"{d.parent.name}/{d.name}/sprite/{d.name}_body.act")
                                           if (d / "sprite" / f"{d.name}_body.act").exists() else None),
                            "weapons": [{**w, "sprite_id": own_sprite_id(f"{d.parent.name}/{d.name}/{w['file']}")}
                                        for w in built.get("weapons", []) if (d / w["file"]).exists()]})
                continue
            try:
                c = self._load(d)
            except Exception as exc:  # broken folder shouldn't hide the rest
                out.append({"name": d.name, "error": str(exc)})
                continue
            out.append({
                "name": c["name"] if NAME_RE.match(c.get("name", "")) else d.name,
                "folder": d.name,
                "group": d.parent.name,
                "animations": list(c["animations"]),
                "frame": [c["frame_width"], c["frame_height"]],
                "pixels_per_meter": c["pixels_per_meter"],
                "warnings": c.get("warnings", []),
            })
        return out

    def load_lab(self, name: str) -> dict:
        p = self._dir(name) / "lab_config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_lab(self, name: str, data: dict) -> dict:
        if not isinstance(data, dict):
            raise CharacterError("Config must be a JSON object.")
        text = json.dumps(data, indent=2)
        if len(text) > 200_000:
            raise CharacterError("Config too large.")
        (self._dir(name) / "lab_config.json").write_text(text, encoding="utf-8")
        return data

    def animset(self, name: str) -> dict:
        d = self._dir(name)
        c = self._load(d)
        fw, fh = c["frame_width"], c["frame_height"]
        px, py = c["pivot"]["x"], c["pivot"]["y"]
        # Layer offset = where the frame's centre sits relative to the pivot (.act convention).
        ox, oy = fw / 2 - px, fh / 2 - py
        dirs = c["directions"]
        mirrored = c.get("mirrored_directions", {})
        rendered = [x for x in dirs if x not in mirrored]
        letters = {x: chr(ord("A") + i) for i, x in enumerate(rendered)}

        names = sorted(c["animations"], key=lambda n: (ORDER_HINT.index(n.lower()) if n.lower() in ORDER_HINT else 99, n))
        atlases, images, actions = [], [], []
        for page, anim_name in enumerate(names):
            a = c["animations"][anim_name]
            sheet = d / a["sheet"]
            version = int(sheet.stat().st_mtime) if sheet.exists() else 0
            atlases.append(f"/renders/{d.name}/{a['sheet']}?v={version}")
            events = a.get("events", {})
            for row, dname in enumerate(dirs):
                frames = []
                for i in range(a["frames"]):
                    images.append([page, i * fw, row * fh, fw, fh])
                    frames.append({
                        "layers": [[len(images) - 1, ox, oy, 0, 1, 1, 0, 255]],
                        "anchors": [],
                        "event": events.get(str(i)),
                    })
                actions.append({
                    "index": len(actions),
                    "type": anim_name,
                    "dir": dname,
                    "delay_ms": a["delay_ms"],
                    "drawing": letters.get(mirrored.get(dname, dname), "?"),
                    "mirrored": dname in mirrored,
                    "frames": frames,
                })

        return {
            "name": c["name"],
            "id": d.name,
            "source": "render",
            "atlases": atlases,
            "images": images,
            "direction_order": dirs,
            "action_types": names,
            "actions": actions,
            "meta": {
                "pixels_per_meter": c["pixels_per_meter"],
                "elevation_deg": c["camera"].get("elevation_deg"),
                "frame": [fw, fh],
                "pivot": c["pivot"],
                "loop": {n: c["animations"][n]["loop"] for n in names},
                "root_motion": {n: c["animations"][n].get("root_motion") for n in names},
                # visible-pixel bounds relative to the pivot: [x0, y0, x1, y1]
                "bounds": {n: c["animations"][n].get("bounds") for n in names},
                "warnings": c.get("warnings", []),
                "lab": self.load_lab(d.name),
            },
        }
