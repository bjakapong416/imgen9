"""
Check a 3D model before rigging/rendering: 8 still views from the game camera + a facts report.

    blender -b -P blender/inspect_model.py -- --files model.fbx [anim.fbx ...] --out <dir>
    blender -b model.blend -P blender/inspect_model.py -- --out <dir>

Writes <dir>/<DIR>.png (S, SW, W, NW, N, NE, E, SE) and <dir>/model_report.json. The stills use
the rest pose, the same camera as render_8dir.py and the same centring / height scaling, so
what you see here is what the sprites will be built from.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_8dir as r  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="inspect_model.py")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=float, default=0)
    ap.add_argument("--facing", default="-Y", choices=list(r.FACING_OFFSET))
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--engine", default="eevee", choices=["eevee", "workbench", "cycles"])
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--elevation", type=float, default=30)
    return ap.parse_args(argv)


def main():
    args = parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.files:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        for f in args.files:
            r.import_file(Path(f).resolve())
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    arms = [o for o in scene.objects if o.type == "ARMATURE"]
    arm = max(arms, key=lambda o: len(o.data.bones)) if arms else None
    if not meshes:
        raise SystemExit("No mesh found in the file(s).")

    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    mats = sorted({m.name for o in meshes for m in o.data.materials if m})
    textures = sorted(i.name for i in bpy.data.images if i.source in ("FILE", "PACKED", "GENERATED") and i.users and i.type == "IMAGE")
    skinned = sum(1 for o in meshes if any(m.type == "ARMATURE" for m in o.modifiers) or (o.parent and o.parent.type == "ARMATURE"))

    turntable, offset = r.make_turntable()
    norm = r.normalize(turntable, offset, meshes, arm, args.height, True)
    if arm is not None:
        arm.data.pose_position = "REST"
    bpy.context.view_layer.update()

    # Frame the camera like render_8dir does.
    dg = bpy.context.evaluated_depsgraph_get()
    rad, zt, zb = r.mesh_bounds(meshes, dg)
    el = math.radians(args.elevation)
    top = zt * math.cos(el) + rad * math.sin(el)
    bottom = rad * math.sin(el) - zb * math.cos(el)
    pivot_frac = min(0.92, max(0.5, top / max(1e-6, top + bottom)))
    pivot_y = round(args.size * pivot_frac)
    ppm = 0.94 * min(args.size / 2 / max(rad, 1e-6), pivot_y / max(top, 1e-6), (args.size - pivot_y) / max(bottom, 1e-6))

    class A:  # minimal args object for the shared setup helpers
        engine, samples, elevation = args.engine, args.samples, args.elevation
    r.setup_render(A, args.size, args.size)
    r.setup_lights()
    r.setup_camera(A, args.size, args.size, ppm, pivot_y)

    facing = r.FACING_OFFSET[args.facing]
    previews = {}
    for d in r.DIRECTIONS:
        turntable.rotation_euler = (0, 0, math.radians(r.DIR_YAW[d] + facing))
        scene.render.filepath = str(out / f"{d}.png")
        bpy.ops.render.render(write_still=True)
        previews[d] = f"{d}.png"

    report = {
        "files": [Path(f).name for f in args.files] or [Path(bpy.data.filepath).name],
        "meshes": len(meshes),
        "triangles": tris,
        "materials": mats,
        "textures": textures,
        "armature": {"name": arm.name, "bones": len(arm.data.bones)} if arm else None,
        "skinned_meshes": skinned,
        "actions": sorted({a.name.split("|")[-1] for a in bpy.data.actions}),
        "original": {"height_m": norm["height_m"], "size_m": norm.get("size_m"), "feet_z": norm.get("feet_z"),
                     "centre_offset_m": [-v for v in norm["moved"][:2]]},
        "scaled_to_m": args.height or None,
        "scale": norm["scale"],
        "facing": args.facing,
        "pixels_per_meter": round(ppm, 2),
        "pivot": {"x": args.size / 2, "y": pivot_y},
        "previews": previews,
    }
    (out / "model_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("[inspect_model] " + json.dumps({k: report[k] for k in ("meshes", "triangles", "armature", "actions", "original")}))


if __name__ == "__main__":
    main()
