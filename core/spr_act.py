"""
Readers and writers for the .spr (images) + .act (animation) sprite format, a common format for
classic 2D online games. The tool saves every character in it and reads it back for previews.

An .act file is an "animation manifest". It answers exactly the questions a sprite pipeline has to
answer - which actions exist, 8 directions per action,
frames per action, how long each frame lasts, where the character's ground origin (pivot)
is, where to attach things (anchors), and on which frame the hit lands (the "atk" event).

Format summary (all little-endian)
----------------------------------
SPR  "SP" + version (minor, major)
     u16 palette_image_count, u16 rgba_image_count (v2.0+)
     palette images : u16 w, u16 h, then (v2.1+) u16 size + RLE bytes (0x00 run-length only)
                      or raw w*h indices; index 0 = transparent
     rgba images    : u16 w, u16 h, w*h*4 bytes in A,B,G,R order, rows stored bottom-up
     last 1024 bytes: 256-colour palette (R,G,B,reserved)

ACT  "AC" + version (minor, major), u16 action_count, 10 reserved bytes
     per action : u32 frame_count, per frame:
                    16 bytes range1 + 16 bytes range2 (legacy, rarely used)
                    u32 layer_count, per layer:
                      i32 x, i32 y          sprite *centre* offset from the origin (= pivot / feet)
                      i32 sprite_index, i32 mirror
                      v2.0+: u8 r,g,b,a  tint
                             f32 scale_x, (v2.4+) f32 scale_y
                             i32 rotation (degrees), i32 sprite_type (0 palette, 1 rgba)
                      v2.5+: i32 width, i32 height
                    v2.0+: i32 event_index (-1 = none)
                    v2.3+: u32 anchor_count, per anchor: 4 skipped bytes, i32 x, i32 y, i32 attr
     v2.1+: u32 event_count, event_count * char[40] names ("atk", "xxx.wav", ...)
     v2.2+: per action f32 interval; frame delay in ms = interval * 25
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# 8 directions per action type. Direction 0 faces the camera and goes clockwise.
ACT_DIRECTIONS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]

# Action type = action_index // 8
MONSTER_ACTIONS = ["idle", "walk", "attack", "damage", "die"]
PLAYER_ACTIONS = [
    "idle", "walk", "sit", "pick", "standby", "attack1", "damage", "freeze",
    "dead", "freeze2", "attack2", "attack3", "cast",
]

FRAME_DELAY_UNIT_MS = 25


class SprActError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise SprActError(f"Unexpected end of file at byte {self.pos} (wanted {n})")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack("<" + fmt, self.take(size))

    def u16(self) -> int: return self.unpack("H")[0]
    def u32(self) -> int: return self.unpack("I")[0]
    def i32(self) -> int: return self.unpack("i")[0]
    def f32(self) -> float: return self.unpack("f")[0]
    def u8(self) -> int: return self.take(1)[0]


# ───────────────────────────── SPR ─────────────────────────────

@dataclass
class SprFile:
    version: float
    palette: list[tuple[int, int, int, int]]
    palette_images: list[Image.Image]   # converted to RGBA with index 0 transparent
    rgba_images: list[Image.Image]

    def image(self, sprite_type: int, index: int) -> Image.Image | None:
        pool = self.rgba_images if sprite_type == 1 else self.palette_images
        return pool[index] if 0 <= index < len(pool) else None


def _decode_rle(data: bytes, expected: int) -> bytes:
    """SPR v2.1 RLE: a 0x00 byte is followed by a run length; other bytes are literal."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n and len(out) < expected:
        b = data[i]
        if b == 0 and i + 1 < n:
            out.extend(b"\x00" * max(1, data[i + 1]))
            i += 2
        else:
            out.append(b)
            i += 1
    if len(out) < expected:
        out.extend(b"\x00" * (expected - len(out)))
    return bytes(out[:expected])


def read_spr(data: bytes) -> SprFile:
    r = _Reader(data)
    if r.take(2) != b"SP":
        raise SprActError("Not an SPR file")
    minor, major = r.u8(), r.u8()
    version = major + minor / 10
    pal_count = r.u16()
    rgba_count = r.u16() if version >= 2.0 else 0

    raw_palette_imgs = []
    for _ in range(pal_count):
        w, h = r.u16(), r.u16()
        if version >= 2.1:
            size = r.u16()
            pixels = _decode_rle(r.take(size), w * h)
        else:
            pixels = r.take(w * h)
        raw_palette_imgs.append((w, h, pixels))

    rgba_imgs = []
    for _ in range(rgba_count):
        w, h = r.u16(), r.u16()
        abgr = r.take(w * h * 4)
        img = Image.frombytes("RGBA", (w, h), abgr, "raw", "ABGR")
        rgba_imgs.append(img.transpose(Image.FLIP_TOP_BOTTOM))

    palette_bytes = data[-1024:]
    palette = [tuple(palette_bytes[i:i + 4]) for i in range(0, 1024, 4)]
    flat_rgb = bytes(b for (pr, pg, pb, _) in palette for b in (pr, pg, pb))

    palette_imgs = []
    for w, h, pixels in raw_palette_imgs:
        if w == 0 or h == 0:
            palette_imgs.append(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
            continue
        p = Image.frombytes("P", (w, h), pixels)
        p.putpalette(flat_rgb)
        p.info["transparency"] = 0
        palette_imgs.append(p.convert("RGBA"))

    return SprFile(version, palette, palette_imgs, rgba_imgs)


# ───────────────────────────── ACT ─────────────────────────────

@dataclass
class ActLayer:
    x: int
    y: int
    sprite_index: int
    mirror: bool
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: int = 0
    sprite_type: int = 0
    width: int = 0
    height: int = 0


@dataclass
class ActAnchor:
    x: int
    y: int
    attr: int


@dataclass
class ActFrame:
    range1: tuple[int, int, int, int]
    range2: tuple[int, int, int, int]
    layers: list[ActLayer]
    event_index: int = -1
    anchors: list[ActAnchor] = field(default_factory=list)


@dataclass
class ActAction:
    frames: list[ActFrame]
    interval: float = 4.0  # multiples of 25 ms (4.0 -> 100 ms, the client default)

    @property
    def delay_ms(self) -> float:
        return self.interval * FRAME_DELAY_UNIT_MS


@dataclass
class ActFile:
    version: float
    actions: list[ActAction]
    events: list[str]
    trailing_bytes: int = 0  # should be 0; non-zero means the layout guess is off


def read_act(data: bytes) -> ActFile:
    r = _Reader(data)
    if r.take(2) != b"AC":
        raise SprActError("Not an ACT file")
    minor, major = r.u8(), r.u8()
    version = round(major + minor / 10, 1)
    action_count = r.u16()
    r.take(10)

    actions: list[ActAction] = []
    for _ in range(action_count):
        frames = []
        for _ in range(r.u32()):
            range1 = r.unpack("4i")
            range2 = r.unpack("4i")
            layers = []
            for _ in range(r.u32()):
                x, y, idx, mirror = r.unpack("4i")
                layer = ActLayer(x, y, idx, bool(mirror))
                if version >= 2.0:
                    layer.color = tuple(r.take(4))
                    layer.scale_x = r.f32()
                    layer.scale_y = r.f32() if version >= 2.4 else layer.scale_x
                    layer.rotation = r.i32()
                    layer.sprite_type = r.i32()
                    if version >= 2.5:
                        layer.width, layer.height = r.i32(), r.i32()
                layers.append(layer)
            frame = ActFrame(range1, range2, layers)
            if version >= 2.0:
                frame.event_index = r.i32()
            if version >= 2.3:
                for _ in range(r.u32()):
                    r.take(4)
                    ax, ay, attr = r.unpack("3i")
                    frame.anchors.append(ActAnchor(ax, ay, attr))
            frames.append(frame)
        actions.append(ActAction(frames))

    events: list[str] = []
    if version >= 2.1:
        for _ in range(r.u32()):
            events.append(r.take(40).split(b"\x00", 1)[0].decode("latin1"))

    if version >= 2.2:
        for action in actions:
            action.interval = r.f32()

    return ActFile(version, actions, events, len(data) - r.pos)


# ───────────────────────────── helpers ─────────────────────────────

def action_name(index: int, kind: str = "monster") -> tuple[str, str]:
    """(action type name, direction) for an action index."""
    names = PLAYER_ACTIONS if kind == "player" else MONSTER_ACTIONS
    t, d = divmod(index, 8)
    return (names[t] if t < len(names) else f"action{t}"), ACT_DIRECTIONS[d]


def load_pair(act_path: Path) -> tuple[ActFile, SprFile]:
    spr_path = act_path.with_suffix(".spr")
    return read_act(act_path.read_bytes()), read_spr(spr_path.read_bytes())


# ───────────────────────────── writers ─────────────────────────────
# Inverse of the readers above, so characters are saved as .spr/.act files
# (SPR 2.1 palette images + ACT 2.5) and opened by the frame-by-frame view or other .act tools.

def _rle_zeros(indices: bytes) -> bytes:
    """SPR 2.1 RLE: runs of index 0 become 0x00 + count (1..255); other bytes are literal."""
    out = bytearray()
    i, n = 0, len(indices)
    while i < n:
        b = indices[i]
        if b == 0:
            run = 1
            while i + run < n and indices[i + run] == 0 and run < 255:
                run += 1
            out += bytes((0, run))
            i += run
        else:
            out.append(b)
            i += 1
    return bytes(out)


def write_spr(images: list[Image.Image], palette: list[tuple[int, int, int]]) -> bytes:
    """images: mode "P" images sharing `palette` (index 0 = transparent). Writes SPR 2.1."""
    out = bytearray(b"SP")
    out += bytes((1, 2))                       # version 2.1 (minor, major)
    out += struct.pack("<HH", len(images), 0)  # palette images, rgba images
    for im in images:
        w, h = im.size
        data = _rle_zeros(im.tobytes())
        out += struct.pack("<HHH", w, h, len(data)) + data
    pal = list(palette)[:256] + [(0, 0, 0)] * (256 - len(palette))
    for r, g, b in pal:
        out += bytes((r, g, b, 0))
    return bytes(out)


def write_act(actions: list[dict], events: list[str]) -> bytes:
    """actions: [{"delay_ms": float, "frames": [{"layers": [(x, y, sprite_index, mirror, w, h)],
                 "event": int (-1 = none)}]}]. Writes ACT 2.5 (one palette-image layer per entry)."""
    out = bytearray(b"AC")
    out += bytes((5, 2))                        # version 2.5
    out += struct.pack("<H", len(actions)) + bytes(10)
    for a in actions:
        out += struct.pack("<I", len(a["frames"]))
        for f in a["frames"]:
            out += struct.pack("<4i", 0, 0, 0, 0) + struct.pack("<4i", 0, 0, 0, 0)
            out += struct.pack("<I", len(f["layers"]))
            for (x, y, idx, mirror, w, h) in f["layers"]:
                out += struct.pack("<4i", int(x), int(y), int(idx), int(bool(mirror)))
                out += bytes((255, 255, 255, 255))           # tint
                out += struct.pack("<ff", 1.0, 1.0)          # scale x, y
                out += struct.pack("<ii", 0, 0)              # rotation, sprite type (0 = palette)
                out += struct.pack("<ii", int(w), int(h))
            out += struct.pack("<i", int(f.get("event", -1)))
            out += struct.pack("<I", 0)                      # anchors
    out += struct.pack("<I", len(events))
    for e in events:
        out += e.encode("latin1")[:39].ljust(40, b"\x00")
    for a in actions:
        out += struct.pack("<f", a["delay_ms"] / FRAME_DELAY_UNIT_MS)
    return bytes(out)
