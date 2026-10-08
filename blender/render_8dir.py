"""
Render a rigged, animated character in 8 isometric directions (Blender 4.2+ / 5.x).

Mixamo / Meshy characters (one FBX/GLB with the mesh, more files with extra animations):
    blender -b -P blender/render_8dir.py -- --name orc --files orc.fbx orc@walk.fbx orc@attack.fbx

A .blend you rigged yourself (e.g. a dragon), picking actions by name:
    blender -b vereth.blend -P blender/render_8dir.py -- --name vereth --actions Idle Walk Bite

Output (default ./renders/<name>/):
    frames/<action>/<DIR>/<index>.png    one transparent PNG per frame
    render_manifest.json                  layout, pivot, scale, timing, root-motion speed, foot steps
The tool packs these into sprite sheets automatically (or: python -m core.sprite_packer renders/<name>).

Conventions (same as .act sprite files, see the frame-by-frame view):
  * Directions are screen-relative: S faces the camera, then clockwise S, SW, W, NW, N, NE, E, SE.
  * The character's ground origin (feet) lands on the same pixel in every frame = the pivot.
  * Root motion is measured (-> move speed in m/s) and removed, so walk cycles play in place.
  * Foot-contact frames of loops are written as "step_l"/"step_r" events (games play step sounds there).

Camera: orthographic, looking down at --elevation degrees. 30 deg gives a classic 2:1
isometric ground plane. Use the same --ppm (pixels per metre) for every character you want to
compare, otherwise each one is scaled to fill its frame.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

DIRECTIONS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
# Yaw (degrees, counter-clockwise about +Z) that turns a model facing -Y (toward the camera)
# to face each screen direction. The camera looks along +Y, so +X is screen-right.
DIR_YAW = {"S": 0, "SE": 45, "E": 90, "NE": 135, "N": 180, "NW": 225, "W": 270, "SW": 315}
# --mirror renders only these and flips the rest (classic 2D games do this for almost every monster).
MIRROR_SOURCES = {"NE": "NW", "E": "W", "SE": "SW"}
FACING_OFFSET = {"-Y": 0, "+Y": 180, "+X": -90, "-X": 90}
LOOP_PATTERN = re.compile(r"idle|walk|run|move|loop|fly|hover|stand|breath", re.I)
FOOT_PATTERNS = {
    "l": re.compile(r"(left.?(toe|foot))|((toe|foot)[._ ]?l(eft)?$)", re.I),
    "r": re.compile(r"(right.?(toe|foot))|((toe|foot)[._ ]?r(ight)?$)", re.I),
}


def log(msg: str) -> None:
    print(f"[render_8dir] {msg}", flush=True)


# ───────────────────────────── arguments ─────────────────────────────

def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(prog="render_8dir.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="character name (output folder name)")
    ap.add_argument("--files", nargs="*", default=[], help=".fbx/.glb/.gltf files; the first one provides the mesh")
    ap.add_argument("--actions", nargs="*", default=[], help="actions to render (default: all)")
    ap.add_argument("--rename", nargs="*", default=[], help="old=new pairs, e.g. mixamo.com=idle")
    ap.add_argument("--out", default=str(here.parent / "renders"), help="output root folder")
    ap.add_argument("--size", default="256", help="frame size: 256 or 256x320")
    ap.add_argument("--ppm", type=float, default=0, help="pixels per metre (0 = auto fit). Keep it equal across characters!")
    ap.add_argument("--elevation", type=float, default=30, help="camera pitch in degrees (30 = 2:1 iso)")
    ap.add_argument("--pivot-y", type=float, default=0, help="pivot height as a fraction of the frame (0 = auto)")
    ap.add_argument("--step", type=int, default=2, help="render every Nth frame (2 -> 30fps anim becomes 15fps)")
    ap.add_argument("--engine", default="eevee", choices=["eevee", "workbench", "cycles"])
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--facing", default="-Y", choices=list(FACING_OFFSET),
                    help="direction the model faces at rest; write it as --facing=-Y (a bare -Y looks like an option)")
    ap.add_argument("--mirror", action="store_true", help="render 5 directions and flip NE/E/SE from NW/W/SW")
    ap.add_argument("--keep-root-motion", action="store_true", help="do not remove root motion")
    ap.add_argument("--root-bone", default="", help="bone that carries root motion (default: auto)")
    ap.add_argument("--directions", nargs="*", default=DIRECTIONS, help="subset of directions (for quick tests)")
    ap.add_argument("--max-frames", type=int, default=0, help="cap frames per action (quick tests)")
    ap.add_argument("--height", type=float, default=0, help="scale the model to this height in metres (0 = keep)")
    ap.add_argument("--no-normalize", action="store_true",
                    help="don't move the model so its feet are at the origin (image-to-3D exports are often off-centre)")
    return ap.parse_args(argv)


# ───────────────────────────── scene loading ─────────────────────────────

def import_file(path: Path) -> list[bpy.types.Object]:
    before = set(bpy.data.objects)
    ext = path.suffix.lower()
    if ext == ".fbx":
        if hasattr(bpy.ops.import_scene, "fbx"):
            bpy.ops.import_scene.fbx(filepath=str(path))
        else:
            bpy.ops.wm.fbx_import(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        raise SystemExit(f"Unsupported file type: {path}")
    return [o for o in bpy.data.objects if o not in before]


def action_name_for(path: Path, original: str, multiple: bool) -> str:
    """'orc@walk.fbx' -> 'walk'; single-anim files are named after the file."""
    if multiple:
        return original.split("|")[-1]
    stem = path.stem
    return stem.split("@", 1)[1] if "@" in stem else stem


def load_files(files: list[str]) -> tuple[bpy.types.Object, list[bpy.types.Action]]:
    # Start from an empty scene.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature = None
    actions: list[bpy.types.Action] = []
    for i, f in enumerate(files):
        path = Path(f).resolve()
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
        before_actions = set(bpy.data.actions)
        new_objs = import_file(path)
        new_actions = [a for a in bpy.data.actions if a not in before_actions]
        for a in new_actions:
            a.use_fake_user = True
            a.name = action_name_for(path, a.name, len(new_actions) > 1)
        actions += new_actions
        arms = [o for o in new_objs if o.type == "ARMATURE"]
        if i == 0:
            if not arms:
                raise SystemExit(f"{path.name} has no armature - rig it first (Mixamo / Rigify / Meshy).")
            armature = arms[0]
        else:
            # Animation-only file: keep its action, drop its objects.
            for o in new_objs:
                bpy.data.objects.remove(o, do_unlink=True)
        log(f"imported {path.name}: {len(new_actions)} action(s) {[a.name for a in new_actions]}")
    return armature, actions


def find_armature() -> bpy.types.Object:
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        raise SystemExit("No armature in this .blend.")
    return max(arms, key=lambda o: len(o.data.bones))


def assign_action(arm: bpy.types.Object, action: bpy.types.Action) -> None:
    # Bones the new action doesn't key would otherwise keep the previous action's pose.
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.rotation_axis_angle = (0, 0, 1, 0)
        pb.scale = (1, 1, 1)
    ad = arm.animation_data or arm.animation_data_create()
    for track in ad.nla_tracks:  # glTF stashes animations in NLA; only the active action should play
        track.mute = True
    ad.action = action
    # Blender 4.4+ slotted actions: make sure a slot is bound.
    if hasattr(ad, "action_slot") and ad.action_slot is None and getattr(action, "slots", None):
        ad.action_slot = action.slots[0]


def find_root_bone(arm: bpy.types.Object, wanted: str) -> str | None:
    bones = arm.pose.bones
    if wanted:
        return wanted if wanted in bones else None
    for name in bones.keys():
        if name.lower().endswith("hips") or name.lower() in ("root", "pelvis", "hip"):
            return name
    roots = [b.name for b in bones if b.parent is None]
    return roots[0] if roots else None


def find_foot_bones(arm: bpy.types.Object) -> dict[str, str]:
    feet = {}
    for side, pat in FOOT_PATTERNS.items():
        # Prefer toe bones (tip touches ground), then feet.
        names = [n for n in arm.pose.bones.keys() if pat.search(n)]
        names.sort(key=lambda n: (0 if "toe" in n.lower() else 1, len(n)))
        if names:
            feet[side] = names[0]
    return feet


# ───────────────────────────── camera / lights ─────────────────────────────

def setup_render(args, width: int, height: int) -> None:
    scene = bpy.context.scene
    r = scene.render
    engines = {
        "eevee": ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"],
        "workbench": ["BLENDER_WORKBENCH"],
        "cycles": ["CYCLES"],
    }[args.engine]
    for e in engines:
        try:
            r.engine = e
            break
        except TypeError:
            continue
    if args.engine == "eevee":
        scene.eevee.taa_render_samples = args.samples
    elif args.engine == "cycles":
        scene.cycles.samples = args.samples
    r.resolution_x, r.resolution_y, r.resolution_percentage = width, height, 100
    r.film_transparent = True
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    try:
        scene.view_settings.view_transform = "Standard"  # true colours, no filmic desaturation
    except TypeError:
        pass


def setup_camera(args, width: int, height: int, ppm: float, pivot_y_px: float) -> bpy.types.Object:
    el = math.radians(args.elevation)
    cam_data = bpy.data.cameras.new("IsoCam")
    cam_data.type = "ORTHO"
    big = max(width, height)
    cam_data.ortho_scale = big / ppm
    # Shift so the world origin (the feet) lands on the pivot pixel.
    cam_data.shift_x = 0.0
    cam_data.shift_y = (pivot_y_px - height / 2) / big
    cam_data.clip_start, cam_data.clip_end = 0.01, 500
    cam = bpy.data.objects.new("IsoCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    dist = 100
    cam.location = (0, -dist * math.cos(el), dist * math.sin(el))
    cam.rotation_euler = (math.pi / 2 - el, 0, 0)
    bpy.context.scene.camera = cam
    return cam


def setup_lights() -> None:
    scene = bpy.context.scene
    if any(o.type == "LIGHT" for o in scene.objects):
        return
    # Fixed to the camera (not the character), so every direction is lit the same way, like hand-drawn sprites.
    for name, energy, rot in (("Key", 3.0, (50, 0, -35)), ("Fill", 1.0, (65, 0, 150))):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        obj.rotation_euler = tuple(math.radians(v) for v in rot)
        scene.collection.objects.link(obj)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.color = (0.35, 0.35, 0.38)
    tree = getattr(scene.world, "node_tree", None)
    if tree and "Background" in tree.nodes:
        bg = tree.nodes["Background"]
        bg.inputs[0].default_value = (0.35, 0.35, 0.38, 1)
        bg.inputs[1].default_value = 0.6


def make_turntable() -> tuple[bpy.types.Object, bpy.types.Object]:
    """Turntable (spins + scales about the origin) > Offset (puts the feet on the origin) > model.
    The camera and lights stay put."""
    scene = bpy.context.scene
    tt = bpy.data.objects.new("Turntable", None)
    offset = bpy.data.objects.new("Offset", None)
    scene.collection.objects.link(tt)
    scene.collection.objects.link(offset)
    offset.parent = tt
    for o in list(scene.objects):
        if o in (tt, offset) or o.parent is not None or o.type in ("CAMERA", "LIGHT"):
            continue
        mw = o.matrix_world.copy()
        o.parent = offset
        o.matrix_world = mw
    return tt, offset


def world_box(meshes) -> tuple[Vector, Vector] | None:
    """Axis-aligned bounds of the evaluated meshes (current pose) in world space."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in meshes:
        ev = o.evaluated_get(dg)
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return (lo, hi) if lo.x != math.inf else None


def feet_centre(meshes, z0: float, z1: float) -> tuple[float, float] | None:
    """Middle of the vertices in the bottom slice of the model (world space, current pose)."""
    dg = bpy.context.evaluated_depsgraph_get()
    xs, ys = [], []
    for o in meshes:
        ev = o.evaluated_get(dg)
        mw = ev.matrix_world
        for v in ev.data.vertices:
            w = mw @ v.co
            if z0 <= w.z <= z1:
                xs.append(w.x)
                ys.append(w.y)
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def normalize(turntable, offset, meshes, arm=None, target_height: float = 0.0, ground: bool = True) -> dict:
    """Centre the model on the origin with its feet at z=0 (rest pose), optionally scale to a height."""
    rest_prev = None
    if arm is not None:
        rest_prev = arm.data.pose_position
        arm.data.pose_position = "REST"
    box = world_box(meshes)
    info = {"height_m": 0.0, "scale": 1.0, "moved": [0.0, 0.0, 0.0]}
    if box:
        lo, hi = box
        info["height_m"] = round(hi.z - lo.z, 4)
        info["size_m"] = [round(hi.x - lo.x, 4), round(hi.y - lo.y, 4), round(hi.z - lo.z, 4)]
        info["feet_z"] = round(lo.z, 4)
        if ground:
            # Centre on the feet, not the whole bounding box: weapons, tails and wings would drag a
            # box centre away from where the character actually stands.
            fx, fy = feet_centre(meshes, lo.z, lo.z + (hi.z - lo.z) * 0.15) or ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2)
            info["feet_centre"] = [round(fx, 4), round(fy, 4)]
            move = Vector((-fx, -fy, -lo.z))
            offset.location = move
            info["moved"] = [round(v, 4) for v in move]
        if target_height and hi.z > lo.z:
            k = target_height / (hi.z - lo.z)
            turntable.scale = (k, k, k)
            info["scale"] = round(k, 5)
    if arm is not None:
        arm.data.pose_position = rest_prev
    bpy.context.view_layer.update()
    return info


# ───────────────────────────── measurement ─────────────────────────────

def frame_list(action: bpy.types.Action, loop: bool, step: int, cap: int) -> list[int]:
    start, end = (int(round(v)) for v in action.frame_range)
    last = end - 1 if loop and end > start else end  # last loop frame usually repeats the first
    frames = list(range(start, last + 1, max(1, step)))
    return frames[:cap] if cap else frames


def bone_world(arm, bone: str) -> Vector:
    pb = arm.pose.bones[bone]
    return arm.matrix_world @ pb.head


def measure_root_motion(arm, root: str | None, action, fps: float) -> dict | None:
    if not root:
        return None
    scene = bpy.context.scene
    start, end = (int(round(v)) for v in action.frame_range)
    if end <= start:
        return None
    scene.frame_set(start)
    p0 = bone_world(arm, root)
    scene.frame_set(end)
    p1 = bone_world(arm, root)
    d = p1 - p0
    dist = math.hypot(d.x, d.y)
    dur = (end - start) / fps
    return {"dx": d.x, "dy": d.y, "distance_m": round(dist, 4), "duration_s": round(dur, 4),
            "speed_m_s": round(dist / dur, 4) if dur else 0.0, "start": start, "end": end}


def root_compensation(rm: dict | None, frame: int) -> Vector:
    """Linear root motion to subtract at a frame (keeps natural sway, removes travel)."""
    if not rm or rm["distance_m"] < 0.02:
        return Vector((0, 0, 0))
    t = (frame - rm["start"]) / max(1, rm["end"] - rm["start"])
    return Vector((rm["dx"] * t, rm["dy"] * t, 0))


def detect_steps(arm, feet: dict[str, str], frames: list[int], set_frame) -> dict[int, str]:
    """Frames where a foot lands: start of each low-height phase, per foot."""
    if not feet:
        return {}
    heights = {side: [] for side in feet}
    for f in frames:
        set_frame(f)
        for side, bone in feet.items():
            heights[side].append(bone_world(arm, bone).z)
    events: dict[int, str] = {}
    for side, hs in heights.items():
        lo, hi = min(hs), max(hs)
        if hi - lo < 0.02:
            continue
        down = lo + (hi - lo) * 0.2   # foot counts as planted below this
        up = lo + (hi - lo) * 0.5     # ...and must lift above this before it can land again
        n = len(hs)
        # The loop repeats, so run it twice: the first pass only settles the airborne state at the
        # seam, the second records contacts.
        airborne = True
        for record in (False, True):
            for i in range(n):
                if airborne and hs[i] <= down:
                    if record:
                        events.setdefault(i, f"step_{side}")
                    airborne = False
                elif hs[i] >= up:
                    airborne = True
    return events


def mesh_bounds(objs, depsgraph) -> tuple[float, float, float]:
    """(max horizontal radius from origin, max z, min z) of evaluated meshes in world space."""
    r = zmax = 0.0
    zmin = 0.0
    for o in objs:
        ev = o.evaluated_get(depsgraph)
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            r = max(r, math.hypot(w.x, w.y))
            zmax = max(zmax, w.z)
            zmin = min(zmin, w.z)
    return r, zmax, zmin


# ───────────────────────────── main ─────────────────────────────

def main() -> None:
    args = parse_args()
    t_start = time.time()

    if args.files:
        arm, actions = load_files(args.files)
    else:
        arm = find_armature()
        actions = list(bpy.data.actions)

    renames = dict(pair.split("=", 1) for pair in args.rename if "=" in pair)
    for a in actions:
        if a.name in renames:
            a.name = renames[a.name]
    if args.actions:
        wanted = {n.lower() for n in args.actions}
        actions = [a for a in actions if a.name.lower() in wanted]
    if not actions:
        raise SystemExit("No actions to render. Available: " + ", ".join(a.name for a in bpy.data.actions))

    if "x" in args.size.lower():
        width, height = (int(v) for v in args.size.lower().split("x"))
    else:
        width = height = int(args.size)

    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    root = find_root_bone(arm, args.root_bone)
    feet = find_foot_bones(arm)
    log(f"armature '{arm.name}', root bone {root}, feet {feet}, scene fps {fps:g}, {len(meshes)} mesh(es)")

    turntable, offset = make_turntable()
    norm = normalize(turntable, offset, meshes, arm, args.height, not args.no_normalize)
    log(f"model {norm.get('size_m')} m, feet at z={norm.get('feet_z')}, moved {norm['moved']}, scale x{norm['scale']}")
    k = turntable.scale.x  # root motion is measured in world units, the armature moves in local units
    base_loc = arm.location.copy()
    facing = FACING_OFFSET[args.facing]
    depsgraph = bpy.context.evaluated_depsgraph_get()

    # Measure every action first (root motion, steps, bounds) with the turntable at rest.
    plans = []
    r_max = z_max = 0.0
    z_min = 0.0
    for action in actions:
        assign_action(arm, action)
        loop = bool(LOOP_PATTERN.search(action.name))
        rm = None if args.keep_root_motion else measure_root_motion(arm, root, action, fps)
        frames = frame_list(action, loop, args.step, args.max_frames)

        def set_frame(f, rm=rm):
            scene.frame_set(f)
            arm.location = base_loc - root_compensation(rm, f) / k
            bpy.context.view_layer.update()

        for f in frames[:: max(1, len(frames) // 12)]:
            set_frame(f)
            r, zt, zb = mesh_bounds(meshes, depsgraph)
            r_max, z_max, z_min = max(r_max, r), max(z_max, zt), min(z_min, zb)
        steps = detect_steps(arm, feet, frames, set_frame) if loop and rm and rm["distance_m"] > 0.05 else {}
        plans.append((action, loop, rm, frames, steps, set_frame))
        speed = f", root motion {rm['speed_m_s']:.2f} m/s" if rm and rm["distance_m"] > 0.02 else ""
        log(f"action '{action.name}': {len(frames)} frames, loop={loop}{speed}, steps at {sorted(steps)}")

    # Fit the camera: everything must stay inside the frame in all 8 directions.
    el = math.radians(args.elevation)
    top = z_max * math.cos(el) + r_max * math.sin(el)          # metres above the pivot on screen
    bottom = r_max * math.sin(el) - z_min * math.cos(el)        # metres below the pivot on screen
    pivot_frac = args.pivot_y or min(0.92, max(0.5, top / max(1e-6, top + bottom)))
    pivot_y_px = round(height * pivot_frac)
    ppm = args.ppm or 0.94 * min(width / 2 / max(r_max, 1e-6), pivot_y_px / max(top, 1e-6),
                                 (height - pivot_y_px) / max(bottom, 1e-6))
    log(f"bounds r={r_max:.2f}m top={top:.2f}m bottom={bottom:.2f}m -> {ppm:.1f} px/m, pivot y {pivot_y_px}px"
        + ("" if args.ppm else "  (tip: pass --ppm to use one scale for every character)"))

    setup_render(args, width, height)
    setup_lights()
    setup_camera(args, width, height, ppm, pivot_y_px)

    out_dir = Path(args.out) / args.name
    frames_dir = out_dir / "frames"
    render_dirs = [d for d in args.directions if not (args.mirror and d in MIRROR_SOURCES)]
    manifest_actions = []
    total = sum(len(p[3]) for p in plans) * len(render_dirs)
    done = 0
    for action, loop, rm, frames, steps, set_frame in plans:
        assign_action(arm, action)
        for d in render_dirs:
            turntable.rotation_euler = (0, 0, math.radians(DIR_YAW[d] + facing))
            target = frames_dir / action.name / d
            target.mkdir(parents=True, exist_ok=True)
            for i, f in enumerate(frames):
                set_frame(f)
                scene.render.filepath = str(target / f"{i:03d}.png")
                bpy.ops.render.render(write_still=True)
                done += 1
            log(f"  {action.name}/{d}: {len(frames)} frames ({done}/{total})")

        anim_fps = fps / max(1, args.step)
        moving = rm and rm["distance_m"] > 0.02
        manifest_actions.append({
            "name": action.name,
            "frames": len(frames),
            "fps": round(anim_fps, 3),
            "delay_ms": round(1000 / anim_fps, 3),
            "loop": loop,
            "source_range": [frames[0], frames[-1]] if frames else [0, 0],
            "step": args.step,
            "root_motion": {k: rm[k] for k in ("distance_m", "duration_s", "speed_m_s")} if moving else None,
            "events": {str(k): v for k, v in sorted(steps.items())},
        })

    manifest = {
        "format": "render_8dir/1",
        "name": args.name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "blender": bpy.app.version_string,
        "frame_width": width,
        "frame_height": height,
        "pivot": {"x": width / 2, "y": pivot_y_px},
        "pixels_per_meter": round(ppm, 3),
        "camera": {"projection": "orthographic", "elevation_deg": args.elevation},
        "directions": DIRECTIONS,
        "rendered_directions": render_dirs,
        "mirrored_directions": {k: v for k, v in MIRROR_SOURCES.items() if k in args.directions} if args.mirror else {},
        "frame_pattern": "frames/{action}/{dir}/{index:03d}.png",
        "actions": manifest_actions,
        "model": norm,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "render_manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"done: {done} frames in {time.time() - t_start:.0f}s -> {out_dir}")


if __name__ == "__main__":
    main()
