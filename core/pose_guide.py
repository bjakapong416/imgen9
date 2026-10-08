"""
Stick-figure pose guides for the AI-drawn sprite sheets.

Image AIs follow a pose they can see far better than a pose described in words (asked in text for
"legs alternate", they repeat one stepping pose). So the tool draws the grid the prompt asks for,
one stick figure per cell, in the same rows and the same 3/4 camera, and the user attaches it next
to the character reference. Blue = the character's LEFT arm/leg, red = RIGHT; a skin-coloured head
with eyes is the front 3/4 view, a grey head is the back of the head.

Poses are joint angles in the character's own space (degrees; + = swung forward), projected with
the same 30-degree camera the prompts ask for. The figure is put on the ground automatically, so
bent knees lower the body by themselves.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from . import sprite2d

# Lengths in head heights (head = 1). Totals: slender 5, chibi 2.5, reference 4 heads tall.
PROPORTIONS = {
    "slender":   {"torso": 1.55, "thigh": 1.2, "shin": 1.25, "upper": 0.85, "fore": 0.8, "shoulder": 0.5, "hip": 0.28},
    "chibi":     {"torso": 0.75, "thigh": 0.38, "shin": 0.37, "upper": 0.38, "fore": 0.32, "shoulder": 0.42, "hip": 0.22},
    "reference": {"torso": 1.25, "thigh": 0.9, "shin": 0.9, "upper": 0.7, "fore": 0.62, "shoulder": 0.48, "hip": 0.26},
}


def _p(**kw) -> dict:
    """One pose. lh/rh: hip swing, lk/rk: knee bend, la/ra: arm swing, le/re: elbow bend,
    lean: torso pitch (+ forward), pitch: whole body (- = falling backward), spread: legs apart,
    w: angle of the weapon in the RIGHT hand (0 = upright, + = tipped forward, -90 = pointing back),
    wl: the same for a weapon in the LEFT hand (a bow)."""
    base = {"lh": 0, "lk": 0, "rh": 0, "rk": 0, "la": 4, "le": 10, "ra": 4, "re": 10,
            "lean": 0, "pitch": 0, "spread": 3, "w": 0, "wl": 0}
    base.update(kw)
    return base


def _mirror(p: dict) -> dict:
    """Same pose with left and right swapped (second half of a walk cycle)."""
    q = dict(p)
    for a, b in (("lh", "rh"), ("lk", "rk"), ("la", "ra"), ("le", "re")):
        q[a], q[b] = p[b], p[a]
    return q


# Walk = a real gait cycle (thigh angle from vertical, knee bend; the curves a mocap walk in
# Blender/Mixamo follows), sampled at 6 even steps and exaggerated x1.3 so it reads at sprite size.
# The leg behind keeps its knee bent with the heel up (toe-off); the swinging leg passes with the
# knee bent ~60 degrees, then reaches forward. Arms swing opposite the legs, elbows bend going forward.
_GAIT = [(22, 5), (15, 18), (2, 8), (-14, 22), (3, 62), (20, 30)]   # (thigh, knee) at 0, 1/6 .. 5/6


def _walk_frame(i: int) -> dict:
    (lh, lk), (rh, rk) = _GAIT[i], _GAIT[(i + 3) % 6]
    la = -20 * math.cos(2 * math.pi * i / 6)
    return _p(lh=lh * 1.3, lk=lk, rh=rh * 1.3, rk=rk, la=la, ra=-la,
              le=12 + max(0, la) * 0.8, re=12 + max(0, -la) * 0.8, lean=3)


_WALK = [_walk_frame(i) for i in range(3)]   # 1 contact LEFT forward, 2 right leg passing, 3 right leg reaching


def _run_frame(i: int) -> dict:
    """Run: the walk gait pushed further (longer reach, knees much higher), body leaning into it,
    elbows bent ~90 degrees pumping opposite the legs."""
    (lh, lk), (rh, rk) = _GAIT[i], _GAIT[(i + 3) % 6]
    la = -40 * math.cos(2 * math.pi * i / 6)
    return _p(lh=lh * 1.9, lk=lk * 1.6 + 10, rh=rh * 1.9, rk=rk * 1.6 + 10, la=la, ra=-la,
              le=85, re=85, lean=14)


_RUN = [_run_frame(i) for i in range(3)]


def _seated(breath: float) -> dict:
    """Sitting on the ground, legs forward and knees up, hands resting on the knees; breath leans
    the body a little."""
    # Thighs 30 degrees above horizontal, shins coming back down at 60 degrees: hips and feet
    # both on the ground with the knees up (the figure is grounded on its lowest joint).
    return _p(lh=120, lk=60, rh=114, rk=56, la=62 + breath * 4, ra=56 + breath * 4, le=48, re=48,
              lean=-4 + breath * 3, spread=12)


def _asleep(breath: float) -> dict:
    """Lying curled on the side, knees drawn up, arms folded: a slow breathing loop."""
    return _p(lh=55, lk=90, rh=50, rk=95, la=60, ra=70, le=110, re=100, lean=10 + breath * 3, pitch=-88)
POSES = {
    "idle": [_p(lk=k, rk=k, lean=1, la=8, ra=8, le=15, re=15, spread=6) for k in (0, 6, 12, 12, 6, 0)],
    "walk": _WALK + [_mirror(p) for p in _WALK],
    "attack": [
        _p(ra=60, re=70, la=10, lean=-4, lh=8, rh=-8, w=-35),                 # wind-up
        _p(ra=165, re=30, la=25, le=30, lean=-10, lh=12, rh=-12, w=-110),     # overhead
        _p(ra=95, re=5, la=20, lean=12, lh=25, lk=20, rh=-18, rk=10, w=110),  # strike (the hit)
        _p(ra=65, re=5, la=15, lean=18, lh=28, lk=28, rh=-20, rk=12, w=135),  # follow-through
        _p(ra=35, re=20, la=10, lean=8, lh=15, lk=10, rh=-10, w=60),          # recover
        _p(ra=10, re=15, la=8, lh=5, rh=-5, w=10),                            # ready
    ],
    "cast": [                                                                     # magic attack: raise, then release forward
        _p(ra=20, re=40, la=20, le=40),                                            # ready
        _p(ra=120, re=40, la=50, le=60, lean=-4, w=-10),                           # gather power, hand rising
        _p(ra=165, re=15, la=70, le=50, lean=-8, lh=6, rh=-6, w=-15),              # hand / weapon high
        _p(ra=88, re=0, la=40, le=50, lean=10, lh=20, lk=14, rh=-14, w=80),        # thrust forward (the release)
        _p(ra=70, re=15, la=30, le=40, lean=6, lh=14, rh=-10, w=60),               # hold
        _p(ra=20, re=30, la=15, le=30, w=10),                                      # recover
    ],
    "damage": [
        _p(lean=-12, la=-25, ra=-25, le=30, re=30, rh=-12, rk=10, w=-15),
        _p(lean=-24, la=-40, ra=-35, le=45, re=45, lk=15, rh=-22, rk=25, w=-25),
        _p(lean=-18, la=-25, ra=-20, le=35, re=35, lk=10, rh=-18, rk=20, w=-20),
        _p(lean=-8, la=-10, ra=-8, rh=-10, rk=8, w=-10),
        _p(lean=-3, rh=-4, w=-5),
        _p(la=5, ra=5),
    ],
    "run": _RUN + [_mirror(p) for p in _RUN],
    "sit": [_seated(b) for b in (0, 0.5, 1, 1, 0.5, 0)],
    "sleep": [_asleep(b) for b in (0, 0.4, 1, 1, 0.4, 0)],
    "happy": [                                                                     # cheer: crouch, jump, arms up
        _p(lk=30, rk=30, lh=15, rh=15, la=10, ra=10, le=20, re=20, lean=6),        # crouch
        _p(lk=5, rk=5, la=150, ra=150, le=10, re=10),                              # spring up, arms raised
        _p(lk=25, rk=35, lh=10, rh=-5, la=170, ra=165, le=5, re=5, lean=-4),       # in the air, legs tucked
        _p(lk=5, rk=5, la=150, ra=140, le=15, re=15),                              # coming down
        _p(lk=30, rk=30, lh=15, rh=15, la=60, ra=60, le=60, re=60, lean=6),        # land, fists pumped
        _p(lk=8, rk=8, la=120, ra=40, le=20, re=80),                               # wave
    ],
    "die": [
        _p(lean=-15, la=-30, ra=-30, le=30, re=30, rh=-15, rk=15),
        _p(lean=8, lh=12, lk=60, rh=-6, rk=60, la=-10, ra=-15, le=30, re=30),
        _p(lean=10, lh=40, lk=100, rh=35, rk=100, la=10, ra=10, pitch=-15),
        _p(lean=-10, lk=40, rk=40, la=-60, ra=-50, pitch=-55),
        _p(lk=20, rk=30, la=-90, ra=-80, pitch=-80),
        _p(lk=10, rk=15, la=-100, ra=-80, le=10, re=10, pitch=-90),
    ],
}

# Attack per weapon style (weapons.TYPES): weapons that attack the same way share one body drawing.
# The hit is frame 4 for the new styles (~59 % through, the usual hit point).
ATTACKS = {
    "swing": POSES["attack"],
    "thrust": [                                                                    # spear, dagger: stab forward
        _p(ra=25, re=70, la=35, le=70, lh=10, rh=-10, w=80),                       # ready, point forward
        _p(ra=-10, re=95, la=20, le=80, lean=-6, lh=8, rh=-14, w=85),              # pull back
        _p(ra=60, re=30, la=45, le=40, lean=8, lh=22, lk=18, rh=-18, rk=8, w=88),  # lunge
        _p(ra=85, re=0, la=60, le=15, lean=16, lh=32, lk=28, rh=-22, rk=12, w=90), # full thrust (the hit)
        _p(ra=55, re=30, la=40, le=40, lean=8, lh=18, lk=12, rh=-14, w=85),        # draw back
        _p(ra=25, re=70, la=35, le=70, lh=10, rh=-10, w=80),                       # ready
    ],
    "bow": [                                                                       # bow in the LEFT hand
        _p(la=20, le=20, ra=10, re=20, wl=10),                                     # bow lowered
        _p(la=70, le=10, ra=60, re=60, lean=2, lh=8, rh=-8),                       # raise, nock
        _p(la=90, le=0, ra=85, re=130, lh=10, rh=-10),                             # draw to the chin
        _p(la=90, le=0, ra=40, re=160, lean=-2, lh=10, rh=-10),                    # release (the hit)
        _p(la=80, le=5, ra=30, re=60, lh=8, rh=-8),                                # follow-through
        _p(la=25, le=20, ra=10, re=20, wl=10),                                     # lower
    ],
    "gun": [                                                                       # one-handed pistol
        _p(ra=20, re=30, la=6, w=115),                                             # pointing down
        _p(ra=60, re=20, lh=6, rh=-6, w=95),                                       # raise
        _p(ra=90, re=0, la=20, le=60, lh=10, rh=-10, w=90),                        # aim
        _p(ra=100, re=10, la=20, le=60, lean=-4, lh=10, rh=-10, w=65),             # fire, recoil (the hit)
        _p(ra=90, re=0, la=20, le=60, lh=10, rh=-10, w=90),                        # aim again
        _p(ra=40, re=20, la=8, w=105),                                             # lower
    ],
}
WEAPON_ARM_SWING = 0.3   # walking with a weapon: that arm swings 30 % as far, keeping the weapon steady


def poses(action: str, style: str | None = None, hand: str | None = None) -> list[dict]:
    """The guide poses for one action. style: the weapon's attack style; hand: "r" / "l" holds a
    weapon (its arm swings less in the walk), None = empty-handed."""
    if action == "attack":
        return ATTACKS.get(style or "swing", ATTACKS["swing"])
    out = POSES[action]
    if action == "walk" and hand in ("r", "l"):
        k = hand + "a"
        out = [{**p, k: p[k] * WEAPON_ARM_SWING, hand + "e": 15} for p in out]
    return out


SKIN, BACK_HEAD, TORSO = (243, 217, 194), (150, 150, 150), (40, 40, 40)
LEFT, RIGHT = (42, 109, 244), (232, 68, 58)
ELEVATION = math.radians(30)


def _joints(pose: dict, pr: dict) -> dict[str, tuple[float, float, float]]:
    """Joint positions (right, up, forward) in head units, feet on the ground (up = 0)."""
    def swing(base, length, deg, side=0.0):
        a = math.radians(deg)
        return (base[0] + side * length, base[1] - math.cos(a) * length, base[2] + math.sin(a) * length)

    lean = math.radians(pose["lean"])
    up = (0.0, math.cos(lean), math.sin(lean))
    pelvis = (0.0, 0.0, 0.0)
    neck = tuple(pelvis[i] + up[i] * pr["torso"] for i in range(3))
    j = {"pelvis": pelvis, "neck": neck, "head": tuple(neck[i] + up[i] * 0.55 for i in range(3))}
    spread = math.sin(math.radians(pose["spread"]))
    for side, s in (("l", -1), ("r", 1)):
        hip = (s * pr["hip"], 0.0, 0.0)
        knee = swing(hip, pr["thigh"], pose[side + "h"], s * spread)
        foot = swing(knee, pr["shin"], pose[side + "h"] - pose[side + "k"], s * spread)
        sh = (s * pr["shoulder"], neck[1] - up[1] * 0.12, neck[2] - up[2] * 0.12)
        elbow = swing(sh, pr["upper"], pose[side + "a"], s * 0.12)
        hand = swing(elbow, pr["fore"], pose[side + "a"] + pose[side + "e"])
        toe = (foot[0], foot[1], foot[2] + 0.28)
        j.update({side + "hip": hip, side + "knee": knee, side + "foot": foot, side + "toe": toe,
                  side + "sh": sh, side + "elbow": elbow, side + "hand": hand})
    for side, key in (("r", "w"), ("l", "wl")):              # 1 unit along the weapon from each hand
        w = math.radians(pose[key])
        hand = j[side + "hand"]
        j[side + "weapon"] = (hand[0], hand[1] + math.cos(w), hand[2] + math.sin(w))
    # Whole-body fall: rotate around the pelvis toward (+) or away from (-) the facing direction.
    p = math.radians(pose["pitch"])
    if p:
        j = {k: (x, u * math.cos(p) - f * math.sin(p), u * math.sin(p) + f * math.cos(p)) for k, (x, u, f) in j.items()}
    ground = min(min(u for _, u, _ in j.values()), j["head"][1] - 0.5)
    return {k: (x, u - ground, f) for k, (x, u, f) in j.items()}


def _project(pt, view: str) -> tuple[float, float, float]:
    """(screen x, screen y down, depth toward camera) for the front 3/4, side or back 3/4 view."""
    s = math.sqrt(0.5)
    fx, fz = {"front": (-s, s), "side": (-1.0, 0.0)}.get(view, (-s, -s))   # bottom-left / left / top-left
    rx, rz = -fz, fx
    r, u, f = pt
    X, Y, Z = r * rx + f * fx, u, r * rz + f * fz
    return X, -Y * math.cos(ELEVATION) + Z * math.sin(ELEVATION), Z


def _draw_figure(d: ImageDraw.ImageDraw, pose: dict, view: str, pr: dict, cx: float, base_y: float, scale: float):
    j = _joints(pose, pr)
    scr = {k: _project(v, view) for k, v in j.items()}
    if abs(pose["pitch"]) > 30:                             # lying down: centre the whole body
        xs = [x for x, _, _ in scr.values()]
        ox = cx - (min(xs) + max(xs)) / 2 * scale
    else:                                                   # standing: centre between the feet
        ox = cx - (scr["lfoot"][0] + scr["rfoot"][0]) / 2 * scale
    to = lambda k: (ox + scr[k][0] * scale, base_y + scr[k][1] * scale)
    width = max(3, round(scale * 0.13))
    segs = []
    for side, col in (("l", LEFT), ("r", RIGHT)):
        for a, b in (("hip", "knee"), ("knee", "foot"), ("foot", "toe"), ("sh", "elbow"), ("elbow", "hand")):
            segs.append(((scr[side + a][2] + scr[side + b][2]) / 2, side + a, side + b, col))
    for a, b in (("pelvis", "neck"), ("lhip", "rhip"), ("lsh", "rsh")):
        segs.append(((scr[a][2] + scr[b][2]) / 2, a, b, TORSO))
    head_depth = scr["head"][2]
    items = [(z, ("seg", a, b, c)) for z, a, b, c in segs] + [(head_depth, ("head",))]
    for _, it in sorted(items, key=lambda t: t[0]):          # far first
        if it[0] == "seg":
            _, a, b, col = it
            d.line([to(a), to(b)], fill=col, width=width)
            for k in (a, b):
                x, y = to(k)
                d.ellipse([x - width * 0.6, y - width * 0.6, x + width * 0.6, y + width * 0.6], fill=col)
        else:
            hx, hy = to("head")
            r = 0.5 * scale
            d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=SKIN if view != "back" else BACK_HEAD,
                      outline=TORSO, width=max(2, width // 2))
            if view != "back":                               # eyes on the facing side
                fdx, fdy, _ = _project((0, 0, 1), view)
                for side in (-1, 1):
                    rdx, rdy, _ = _project((side * 0.22, 0, 0), view)
                    ex, ey = hx + (fdx * 0.3 + rdx) * scale, hy + (fdy * 0.3 + rdy - 0.05) * scale
                    d.ellipse([ex - width * 0.5, ey - width * 0.5, ex + width * 0.5, ey + width * 0.5], fill=TORSO)


def standing_height(pr: dict) -> float:
    """Screen height (head units) of the first idle pose: the yardstick a sprite frame is scaled to."""
    scr = [_project(v, "front") for v in _joints(POSES["idle"][0], pr).values()]
    top = min(y for _, y, _ in scr) - 0.5 * math.cos(ELEVATION)
    return max(y for _, y, _ in scr) - top


def hand_frames(action: str, view: str, body: str = sprite2d.DEFAULT_BODY, style: str | None = None,
                hand: str = "r", rest: float = 0) -> list[dict]:
    """Where the weapon hand is in each guide frame, for placing a weapon layer.

    style: the weapon's attack style (ATTACKS); hand: "r", or "l" for a bow; rest: extra weapon
    angle outside the attack (a gun points down-forward). x, y: hand position relative to the point
    between the feet (the sprite pivot), in units of the standing figure's height, y down. angle:
    weapon direction on screen, degrees clockwise from straight up. behind: the hand is on the far
    side of the body, so the weapon is drawn under it."""
    pr = PROPORTIONS.get(body, PROPORTIONS[sprite2d.DEFAULT_BODY])
    h = standing_height(pr)
    key = "w" if hand == "r" else "wl"
    out = []
    for pose in poses(action, style, hand):
        if action != "attack" and rest:
            pose = {**pose, key: pose[key] + rest}
        j = _joints(pose, pr)
        scr = {k: _project(v, view) for k, v in j.items()}
        if abs(pose["pitch"]) > 30:
            xs = [x for x, _, _ in scr.values()]
            ox = (min(xs) + max(xs)) / 2
        else:
            ox = (scr["lfoot"][0] + scr["rfoot"][0]) / 2
        oy = max(y for _, y, _ in scr.values())
        hx, hy, hz = scr[hand + "hand"]
        wx, wy, _ = scr[hand + "weapon"]
        angle = math.degrees(math.atan2(wx - hx, -(wy - hy)))
        torso_z = (scr["pelvis"][2] + scr["neck"][2]) / 2
        out.append({"x": round((hx - ox) / h, 4), "y": round((hy - oy) / h, 4),
                    "angle": round(angle, 1), "behind": bool(hz < torso_z - 0.05)})
    return out


def render(slots: list[tuple[str, str]], body: str = sprite2d.DEFAULT_BODY, cell: tuple[int, int] = (210, 260),
           style: str | None = None, hand: str | None = None) -> Image.Image:
    """The pose-guide grid for these (action, view) rows: GRID_COLS frames per row, like the prompt.
    style / hand: the character's weapon (see poses)."""
    pr = PROPORTIONS.get(body, PROPORTIONS[sprite2d.DEFAULT_BODY])
    total = 1 + pr["torso"] + pr["thigh"] + pr["shin"]
    cw, ch = cell
    scale = ch * 0.72 / (total * math.cos(ELEVATION))
    cols = sprite2d.GRID_COLS
    img = Image.new("RGB", (cw * cols, ch * len(slots)), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for r, (action, view) in enumerate(slots):
        base_y = r * ch + ch * 0.86
        for c, pose in enumerate(poses(action, style, hand)[:cols]):
            _draw_figure(d, pose, view, pr, c * cw + cw / 2, base_y, scale)
    return img


def png(slots: list[tuple[str, str]], body: str = sprite2d.DEFAULT_BODY, style: str | None = None,
        hand: str | None = None) -> bytes:
    buf = io.BytesIO()
    render(slots, body, style=style, hand=hand).save(buf, "PNG", optimize=True)
    return buf.getvalue()
