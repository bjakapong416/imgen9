"""
The sprites made with this tool (renders/<project>/<character>/sprite/*.spr + .act), served to the browser in the
tool's common "animset" format.

animset (shared with rendered characters, see core/characters.py)
{
  "name", "source": "sprite",
  "atlases": [url, ...],                     # texture pages
  "images":  [[page, x, y, w, h], ...],      # sprite rects inside the pages
  "direction_order": [...], "action_types": [...],
  "actions": [{
      "index", "type", "dir", "delay_ms", "drawing", "mirrored",
      "frames": [{"layers": [[image, x, y, mirror, scale_x, scale_y, rotation, alpha]],
                  "anchors": [[x, y]], "event": "atk" | "step" | null}]
  }],
  "meta": {...}
}
Layer x, y = where the sprite's *centre* goes relative to the origin (the character's ground
position / pivot), as in .act files; the frontend renderer uses the same convention.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .i18n import _
from .spr_act import MONSTER_ACTIONS, PLAYER_ACTIONS, ACT_DIRECTIONS, read_act, read_spr

ATLAS_MAX_W = 2048
ATLAS_MAX_H = 4096
PAD = 1
SPRITE_DIR = "sprite"      # renders/<project>/<character>/sprite/


def own_sprite_id(rel: str) -> str:
    """Id of a sprite, from its path under renders/ ("<project>/<character>/sprite/<character>.act")."""
    return "own_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]


def _own_scale(act: Path) -> dict:
    """{"pixels_per_meter": ...} from the game.json a 2D build writes next to its sprites."""
    folder = next((p for p in act.parents if p.name == SPRITE_DIR), None)
    try:
        ppm = json.loads((folder / "game.json").read_text(encoding="utf-8")).get("pixels_per_metre") if folder else None
    except (OSError, ValueError):
        return {}
    return {"pixels_per_meter": ppm} if ppm else {}


def _variant_name(act: Path) -> str:
    """A colour variant's name from the build report next to it, else its id."""
    try:
        rep = json.loads((act.parents[1] / "build.json").read_text(encoding="utf-8"))
        return str((rep.get("variant_names") or {}).get(act.stem) or act.stem)
    except (OSError, ValueError):
        return act.stem


class SpriteError(ValueError):
    pass


@dataclass
class SpriteEntry:
    id: str         # short stable id used in URLs
    rel: str        # path relative to renders/, forward slashes
    name: str       # display name
    folder: str     # display folder


class SpriteLibrary:
    def __init__(self, renders_dir: Path, cache_dir: Path):
        # Rescanned on every listing, so a new build shows up without a restart.
        self.root = renders_dir.resolve()
        self.cache_dir = cache_dir
        self._by_id: dict[str, SpriteEntry] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    # ── index ──
    def index(self) -> list[SpriteEntry]:
        if not self.available:
            return []
        out = []
        for act in sorted([*self.root.glob(f"*/*/{SPRITE_DIR}/*.act"), *self.root.glob(f"*/*/{SPRITE_DIR}/weapons/*.act"),
                           *self.root.glob(f"*/*/{SPRITE_DIR}/variants/*.act")]):
            if act.with_suffix(".spr").exists():
                rel = act.relative_to(self.root).as_posix()
                # A colour variant is listed as "<character> (<variant name>)".
                name = f"{act.parents[2].name} ({_variant_name(act)})" if act.parent.name == "variants" else act.stem
                # Rescanned on every listing (inside a request), so the label follows the request's language.
                out.append(SpriteEntry(own_sprite_id(rel), rel, name, _("ours (renders)")))
        with self._lock:
            self._by_id = {e.id: e for e in out}
        return out

    def search(self, q: str = "", limit: int = 300, project: str | None = None) -> dict:
        q = q.strip().lower()
        items = [e for e in self.index() if "/sprite/weapons/" not in e.rel]   # weapon variants: Playtest only
        if project:                     # one character: renders/<project>/<character>/sprite/...
            items = [e for e in items if e.rel.split("/")[1] == project]
        if q:
            items = [e for e in items if q in e.name.lower() or q in e.folder.lower()]
        return {
            "total": len(items),
            "items": [{"id": e.id, "name": e.name, "folder": e.folder} for e in items[:limit]],
        }

    def resolve(self, sprite_id: str) -> Path:
        self.index()
        entry = self._by_id.get(sprite_id)
        if not entry:
            raise SpriteError(_("Unknown sprite id."))
        return self.root / entry.rel

    # ── atlas ──
    def _cache_key(self, act_path: Path) -> str:
        spr = act_path.with_suffix(".spr")
        sig = f"{act_path}|{act_path.stat().st_mtime_ns}|{spr.stat().st_mtime_ns}"
        return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]

    def _build_atlas(self, act_path: Path, key: str) -> dict:
        """Pack every SPR image into texture pages; returns {"images": [...], "pages": n}."""
        spr = read_spr(act_path.with_suffix(".spr").read_bytes())
        sources = [(0, i, im) for i, im in enumerate(spr.palette_images)]
        sources += [(1, i, im) for i, im in enumerate(spr.rgba_images)]

        # Shelf packing, tallest first.
        order = sorted(range(len(sources)), key=lambda k: -sources[k][2].height)
        pages: list[list[tuple[int, int, int]]] = [[]]
        rects: dict[int, tuple[int, int, int, int, int]] = {}
        x = y = shelf_h = 0
        page_heights = [0]
        for k in order:
            w, h = sources[k][2].size
            if x + w + PAD > ATLAS_MAX_W:
                x, y, shelf_h = 0, y + shelf_h + PAD, 0
            if y + h > ATLAS_MAX_H:
                pages.append([])
                page_heights.append(0)
                x = y = shelf_h = 0
            page = len(pages) - 1
            pages[page].append((k, x, y))
            rects[k] = (page, x, y, w, h)
            page_heights[page] = max(page_heights[page], y + h)
            x += w + PAD
            shelf_h = max(shelf_h, h)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for pi, placed in enumerate(pages):
            width = max((px + sources[k][2].width for k, px, _ in placed), default=1)
            sheet = Image.new("RGBA", (max(1, width), max(1, page_heights[pi])), (0, 0, 0, 0))
            for k, px, py in placed:
                sheet.paste(sources[k][2], (px, py))
            sheet.save(self.cache_dir / f"{key}_{pi}.png")

        # images keyed by (type, index) so ACT layers can look them up
        lookup = {f"{sources[k][0]}:{sources[k][1]}": list(rects[k]) for k in rects}
        meta = {"pages": len(pages), "lookup": lookup}
        (self.cache_dir / f"{key}.json").write_text(json.dumps(meta))
        return meta

    def atlas_meta(self, act_path: Path) -> tuple[str, dict]:
        key = self._cache_key(act_path)
        meta_path = self.cache_dir / f"{key}.json"
        if meta_path.exists():
            return key, json.loads(meta_path.read_text())
        return key, self._build_atlas(act_path, key)

    def atlas_page(self, sprite_id: str, page: int) -> Path:
        act_path = self.resolve(sprite_id)
        key, meta = self.atlas_meta(act_path)
        if not 0 <= page < meta["pages"]:
            raise SpriteError("No such atlas page.")
        return self.cache_dir / f"{key}_{page}.png"

    # ── animset ──
    def animset(self, sprite_id: str, atlas_url) -> dict:
        act_path = self.resolve(sprite_id)
        act = read_act(act_path.read_bytes())
        key, meta = self.atlas_meta(act_path)
        lookup = meta["lookup"]

        images: list[list[int]] = []
        image_ids: dict[str, int] = {}

        def image_id(sprite_type: int, index: int) -> int:
            k = f"{sprite_type}:{index}"
            if k not in lookup:
                return -1
            if k not in image_ids:
                image_ids[k] = len(images)
                images.append(lookup[k])
            return image_ids[k]

        n_types = len(act.actions) // 8
        kind = "player" if n_types > 9 else "monster"
        type_names = PLAYER_ACTIONS if kind == "player" else MONSTER_ACTIONS
        from .sprite_images import type_names_for

        type_names = type_names_for(act_path) or type_names
        types = [type_names[t] if t < len(type_names) else f"action{t}" for t in range(max(1, n_types))]

        actions = []
        drawing_letters: dict[tuple[int, tuple], str] = {}
        for ai, action in enumerate(act.actions):
            t, d = divmod(ai, 8)
            frames = []
            for f in action.frames:
                layers = []
                for L in f.layers:
                    if L.sprite_index < 0:
                        continue
                    img = image_id(L.sprite_type, L.sprite_index)
                    if img < 0:
                        continue
                    layers.append([img, L.x, L.y, int(L.mirror), round(L.scale_x, 4), round(L.scale_y, 4),
                                   L.rotation, L.color[3]])
                event = act.events[f.event_index] if 0 <= f.event_index < len(act.events) else None
                frames.append({"layers": layers, "anchors": [[a.x, a.y] for a in f.anchors], "event": event or None})

            # Which artwork does this direction use? Same letter = same drawing (possibly flipped).
            key_t = tuple(tuple((L.sprite_type, L.sprite_index) for L in fr.layers if L.sprite_index >= 0)
                          for fr in action.frames)
            per_type = {k: v for k, v in drawing_letters.items() if k[0] == t}
            if (t, key_t) not in drawing_letters:
                drawing_letters[(t, key_t)] = chr(ord("A") + len(per_type))
            first_layers = [L for L in (action.frames[0].layers if action.frames else []) if L.sprite_index >= 0]

            actions.append({
                "index": ai,
                "type": types[t] if t < len(types) else f"action{t}",
                "dir": ACT_DIRECTIONS[d],
                "delay_ms": round(action.delay_ms, 3),
                "interval": round(action.interval, 4),
                "drawing": drawing_letters[(t, key_t)],
                "mirrored": bool(first_layers) and all(L.mirror for L in first_layers),
                "frames": frames,
            })

        return {
            "name": act_path.stem,
            "source": "sprite",
            "id": sprite_id,
            # &v= changes whenever the sprite is rebuilt, so a cached page never pairs with new rects
            "atlases": [f"{atlas_url(sprite_id, p)}&v={key}" for p in range(meta["pages"])],
            "images": images,
            "direction_order": ACT_DIRECTIONS,
            "action_types": types,
            "actions": actions,
            "meta": {
                **_own_scale(act_path),
                "act_version": act.version,
                "kind": kind,
                "events": act.events,
                "action_count": len(act.actions),
                "folder": self._by_id[sprite_id].folder,
            },
        }
