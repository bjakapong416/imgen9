"""
Render a model WITHOUT a rig in 8 directions with whole-body procedural actions - the way many
classic 2D game monsters move (blobs and the like: squash, hop, lunge, recoil, fall).

    blender -b -P blender/render_procedural.py -- --name <id> --files model.fbx --out renders
            [--size 256] [--ppm 64] [--height 1.6] [--facing=-Y] [--extras happy,sit,sleep]

Writes the same renders/<id>/frames/... + render_manifest.json as render_8dir.py, so packing,
Playtest and the .spr/.act export work unchanged. Legs don't step (no bones); once the model is rigged,
render_8dir.py replaces these with real animations.

Typical classic 2D game defaults: idle 8 x 100 ms, walk 8 x 75 ms,
attack 12 x 75 ms with the hit at ~59 %, damage 5 x 75 ms, die 10 x 100 ms.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_8dir as r  # noqa: E402

FORWARD = {"-Y": Vector((0, -1, 0)), "+Y": Vector((0, 1, 0)), "+X": Vector((1, 0, 0)), "-X": Vector((-1, 0, 0))}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(prog="render_procedural.py")
    ap.add_argument("--name", required=True)
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--out", default=str(here.parent / "renders"))
    ap.add_argument("--size", default="256")
    ap.add_argument("--ppm", type=float, default=0)
    ap.add_argument("--elevation", type=float, default=30)
    ap.add_argument("--height", type=float, default=0)
    ap.add_argument("--facing", default="-Y", choices=list(r.FACING_OFFSET))
    ap.add_argument("--engine", default="eevee", choices=["eevee", "workbench", "cycles"])
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--extras", default="", help="comma list of extra actions: happy, sit, sleep")
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--directions", nargs="*", default=r.DIRECTIONS)
    return ap.parse_args(argv)


def ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


# Each action: frames, delay, loop, and pose(t, i) -> (offset along forward, up, side; pitch, roll; sx, sz).
# Distances are in units of the character height H; angles in degrees (pitch > 0 leans forward).
def pose_idle(t, i):
    s = math.sin(2 * math.pi * t)
    return 0, 0, 0, 0, 0, 1 - 0.012 * s, 1 + 0.025 * s


def pose_walk(t, i):
    s = math.sin(2 * math.pi * t)
    hop = abs(math.sin(2 * math.pi * t))
    squash = 1 - 0.04 * (1 - hop)          # squash on landing, like a bouncing blob
    return 0, 0.04 * hop, 0, 5, 4 * s, 1 + 0.02 * (1 - hop), squash


def pose_attack(t, i):
    if t < 0.4:                             # wind-up: lean back
        k = ease(t / 0.4)
        return -0.08 * k, 0, 0, -10 * k, 0, 1, 1 - 0.04 * k
    if t < 0.62:                            # strike: lunge forward and stretch
        k = ease((t - 0.4) / 0.22)
        return -0.08 + 0.33 * k, 0.02 * k, 0, -10 + 28 * k, 0, 1 - 0.05 * k, 1 + 0.05 * k
    k = ease((t - 0.62) / 0.38)             # recover
    return 0.25 * (1 - k), 0.02 * (1 - k), 0, 18 * (1 - k), 0, 1 - 0.05 * (1 - k), 1 + 0.05 * (1 - k)


def pose_damage(t, i):
    k = math.sin(math.pi * min(1.0, t * 1.25))
    return -0.1 * k, 0, 0, -14 * k, 0, 1 + 0.03 * k, 1 - 0.05 * k


def pose_die(t, i):
    k = ease(t / 0.7)
    return 0, -0.02 * k, 0, 0, 86 * k, 1, 1 - 0.1 * k


def pose_happy(t, i):
    hop = abs(math.sin(4 * math.pi * t))
    return 0, 0.12 * hop, 0, 0, 6 * math.sin(4 * math.pi * t), 1 + 0.04 * hop, 1 - 0.06 * (1 - hop)


def pose_sit(t, i):
    s = math.sin(2 * math.pi * t)
    return 0, 0, 0, -6, 0, 1.06, 0.8 + 0.01 * s


def pose_sleep(t, i):
    s = math.sin(2 * math.pi * t)
    return 0, -0.02, 0, 0, 82, 1, 0.92 + 0.02 * s


ACTIONS = {
    "idle": (8, 100, True, pose_idle, {}),
    "walk": (8, 75, True, pose_walk, {0: "step_l", 4: "step_r"}),
    "attack": (12, 75, False, pose_attack, {}),
    "hurt": (5, 75, False, pose_damage, {}),
    "die": (10, 100, False, pose_die, {}),
    "happy": (8, 100, True, pose_happy, {}),
    "sit": (6, 150, True, pose_sit, {}),
    "sleep": (6, 200, True, pose_sleep, {}),
}


def main():
    args = parse_args()
    t_start = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for f in args.files:
        r.import_file(Path(f).resolve())
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    if not meshes:
        raise SystemExit("No mesh to render.")
    width = height = int(args.size)

    # Turntable (spin + scale) > Motion (procedural action) > Offset (feet on origin) > model
    turntable, offset = r.make_turntable()
    motion = bpy.data.objects.new("Motion", None)
    scene.collection.objects.link(motion)
    motion.parent = turntable
    offset.parent = motion
    norm = r.normalize(turntable, offset, meshes, None, args.height, True)
    H = norm["height_m"] or 1.0                     # model units (before the turntable scale)
    fwd = FORWARD[args.facing]
    up = Vector((0, 0, 1))
    side = fwd.cross(up).normalized()               # tilt axis for leaning forward/back

    def apply(pose):
        df, du, ds, pitch, roll, sxy, sz = pose
        loc = fwd * (df * H) + up * (du * H) + side * (ds * H)
        rot = Matrix.Rotation(math.radians(-pitch), 4, side) @ Matrix.Rotation(math.radians(roll), 4, fwd)
        # squash/stretch in the character's own frame (forward, side, up)
        basis = Matrix((side, fwd, up)).transposed().to_4x4()
        scale = basis @ Matrix.Diagonal((sxy, sxy, sz, 1)) @ basis.inverted()
        motion.matrix_basis = Matrix.Translation(loc) @ rot @ scale
        bpy.context.view_layer.update()

    wanted = ["idle", "walk", "attack", "hurt", "die"] + [e for e in args.extras.split(",") if e in ACTIONS]
    # Frame the camera over every pose of every action (lunges and falls included).
    dg = bpy.context.evaluated_depsgraph_get()
    r_max = z_max = 0.0
    z_min = 0.0
    for name in wanted:
        n, _, loop, fn, _ = ACTIONS[name]
        for i in range(n):
            apply(fn(i / n if loop else i / max(1, n - 1), i))
            rr, zt, zb = r.mesh_bounds(meshes, dg)
            r_max, z_max, z_min = max(r_max, rr), max(z_max, zt), min(z_min, zb)
    el = math.radians(args.elevation)
    top = z_max * math.cos(el) + r_max * math.sin(el)
    bottom = r_max * math.sin(el) - z_min * math.cos(el)
    pivot_y = round(height * min(0.92, max(0.5, top / max(1e-6, top + bottom))))
    ppm = args.ppm or 0.94 * min(width / 2 / max(r_max, 1e-6), pivot_y / max(top, 1e-6), (height - pivot_y) / max(bottom, 1e-6))

    class A:
        engine, samples, elevation = args.engine, args.samples, args.elevation
    r.setup_render(A, width, height)
    r.setup_lights()
    r.setup_camera(A, width, height, ppm, pivot_y)

    out_dir = Path(args.out) / args.name
    render_dirs = [d for d in args.directions if not (args.mirror and d in r.MIRROR_SOURCES)]
    facing = r.FACING_OFFSET[args.facing]
    manifest_actions = []
    for name in wanted:
        n, delay, loop, fn, events = ACTIONS[name]
        for d in render_dirs:
            turntable.rotation_euler = (0, 0, math.radians(r.DIR_YAW[d] + facing))
            target = out_dir / "frames" / name / d
            target.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                apply(fn(i / n if loop else i / max(1, n - 1), i))
                scene.render.filepath = str(target / f"{i:03d}.png")
                bpy.ops.render.render(write_still=True)
        r.log(f"  {name}: {n} frames x {len(render_dirs)} dirs")
        manifest_actions.append({
            "name": name, "frames": n, "fps": round(1000 / delay, 3), "delay_ms": delay, "loop": loop,
            "source_range": [0, n - 1], "step": 1, "root_motion": None,
            "events": {str(k): v for k, v in events.items()},
        })

    manifest = {
        "format": "render_8dir/1", "generator": "render_procedural.py", "procedural": True,
        "name": args.name, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "blender": bpy.app.version_string,
        "frame_width": width, "frame_height": height, "pivot": {"x": width / 2, "y": pivot_y},
        "pixels_per_meter": round(ppm, 3), "camera": {"projection": "orthographic", "elevation_deg": args.elevation},
        "directions": r.DIRECTIONS, "rendered_directions": render_dirs,
        "mirrored_directions": {k: v for k, v in r.MIRROR_SOURCES.items()} if args.mirror else {},
        "frame_pattern": "frames/{action}/{dir}/{index:03d}.png", "actions": manifest_actions, "model": norm,
    }
    (out_dir / "render_manifest.json").write_text(json.dumps(manifest, indent=2))
    r.log(f"done (procedural): {len(wanted)} actions in {time.time() - t_start:.0f}s -> {out_dir}")


if __name__ == "__main__":
    main()
