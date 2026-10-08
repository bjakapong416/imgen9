"""
Colour a generated (untextured) mesh from the turnaround views, bake it into a texture and export.

    blender -b -P blender/project_texture.py -- --mesh shape.glb --views <dir with front/side/back.png>
            --out <dir>/<name> [--height 1.6] [--tex 1024]

Writes <name>.fbx (texture embedded, ready for Mixamo), <name>.glb and <name>_tex.png.

How the colour is found: every vertex is projected into each view (front from -Y, back from +Y,
side from +X, mirrored side from -X) and the samples are blended by how squarely the surface faces
that view. The trimmed cut-outs line up with the mesh's bounding box, so no camera calibration is
needed. Surfaces no view sees well (top of the head, under the chin) take the nearest-facing view's
colour.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy
import numpy as np


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="project_texture.py")
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--views", required=True)
    ap.add_argument("--out", required=True, help="output path without extension")
    ap.add_argument("--height", type=float, default=0, help="scale to this height in metres")
    ap.add_argument("--tex", type=int, default=1024)
    return ap.parse_args(argv)


def log(msg):
    print(f"[project_texture] {msg}", flush=True)


def load_rgba(path: Path) -> np.ndarray | None:
    """Image as float array [h, w, 4], row 0 = TOP, colours converted to linear."""
    if not path.exists():
        return None
    img = bpy.data.images.load(str(path))
    w, h = img.size
    arr = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    arr = arr.reshape(h, w, 4)[::-1].copy()  # Blender stores bottom row first
    rgb = arr[..., :3]
    arr[..., :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    bpy.data.images.remove(img)
    return arr


def sample(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x = np.clip(np.round(u * (w - 1)).astype(int), 0, w - 1)
    y = np.clip(np.round(v * (h - 1)).astype(int), 0, h - 1)
    return img[y, x]


def main():
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.import_scene.gltf(filepath=args.mesh)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("No mesh in " + args.mesh)

    # One object, transforms applied, feet on the ground, centred, scaled to the target height.
    for o in scene.objects:
        o.select_set(o in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = Path(args.out).name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    lo, hi = co.min(axis=0), co.max(axis=0)
    k = args.height / (hi[2] - lo[2]) if args.height else 1.0
    co = (co - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])) * k
    me.vertices.foreach_set("co", co.ravel())
    me.update()
    lo, hi = co.min(axis=0), co.max(axis=0)
    size = np.maximum(hi - lo, 1e-6)
    log(f"mesh {n} verts, size {size.round(3).tolist()} m")

    nrm = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("normal", nrm)
    nrm = nrm.reshape(n, 3)

    vdir = Path(args.views)
    front, side, back = (load_rgba(vdir / f"{v}.png") for v in ("front", "side", "back"))
    if front is None:
        raise SystemExit("front.png is required")

    up = (hi[2] - co[:, 2]) / size[2]                      # 0 at the top, 1 at the feet
    projections = []                                         # (image, u, facing weight)
    projections.append((front, (co[:, 0] - lo[0]) / size[0], np.maximum(0, -nrm[:, 1])))
    if back is not None:
        projections.append((back, (hi[0] - co[:, 0]) / size[0], np.maximum(0, nrm[:, 1])))
    if side is not None:  # side view faces image-left = looking at the +X side; mirror it for -X
        projections.append((side, (co[:, 1] - lo[1]) / size[1], np.maximum(0, nrm[:, 0])))
        projections.append((side, (hi[1] - co[:, 1]) / size[1], np.maximum(0, -nrm[:, 0])))

    acc = np.zeros((n, 3), dtype=np.float32)
    wsum = np.zeros(n, dtype=np.float32)
    best = np.zeros((n, 3), dtype=np.float32)
    best_w = np.full(n, -1.0, dtype=np.float32)
    for img, u, facing in projections:
        px = sample(img, u, up)
        inside = (px[:, 3] > 0.5).astype(np.float32)     # ignore samples that miss the silhouette
        w = (facing ** 3) * inside
        acc += px[:, :3] * w[:, None]
        wsum += w
        cand = (facing + 0.01) * inside                    # fallback: the most squarely facing hit
        better = cand > best_w
        best[better] = px[better, :3]
        best_w[better] = cand[better]
    col = np.where(wsum[:, None] > 1e-4, acc / np.maximum(wsum, 1e-6)[:, None], best)
    missing = best_w <= 0
    if missing.any():  # no view hit at all: use the average colour
        col[missing] = col[~missing].mean(axis=0) if (~missing).any() else 0.5
    log(f"coloured from {len(projections)} projections; {int(missing.sum())} vertices unseen")

    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    rgba = np.concatenate([col, np.ones((n, 1), dtype=np.float32)], axis=1)
    attr.data.foreach_set("color", rgba.ravel())

    # Material: vertex colour -> base colour (for baking), then swap to the baked image.
    mat = bpy.data.materials.new("Body")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.85
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "Col"
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    me.materials.clear()
    me.materials.append(mat)

    # UVs + bake
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")
    tex = bpy.data.images.new(f"{obj.name}_tex", args.tex, args.tex)
    tnode = nt.nodes.new("ShaderNodeTexImage")
    tnode.image = tex
    nt.nodes.active = tnode
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    try:  # GPU if available; CPU works too, just slower
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = backend
                prefs.get_devices()
                if any(d.type == backend for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = d.type == backend
                    scene.cycles.device = "GPU"
                    break
            except TypeError:
                continue
    except Exception:
        pass
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, margin=4, use_clear=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tex.filepath_raw = str(out.with_name(out.name + "_tex.png"))
    tex.file_format = "PNG"
    tex.save()
    nt.links.new(tnode.outputs["Color"], bsdf.inputs["Base Color"])
    nt.nodes.remove(vc)
    log(f"baked {args.tex}px texture on {scene.cycles.device}")

    bpy.ops.export_scene.fbx(filepath=str(out.with_suffix(".fbx")), path_mode="COPY", embed_textures=True,
                             use_selection=False, add_leaf_bones=False)
    bpy.ops.export_scene.gltf(filepath=str(out.with_suffix(".glb")), export_format="GLB")
    log(f"exported {out.with_suffix('.fbx').name} + .glb")


if __name__ == "__main__":
    main()
