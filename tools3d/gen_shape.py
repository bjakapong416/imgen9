"""
Local image-to-3D: Hunyuan3D-2mv shape from the project's turnaround views.

Run with the Hunyuan3D environment (not the tool's .venv):
    <your Hunyuan3D-2 venv>/python tools3d/gen_shape.py \
        --front views/front.png [--left views/side.png] [--back views/back.png] --out shape.glb

Views are transparent cut-outs. "left" is the side view with the character facing image-left
(the Pipeline flips the side view when needed). Shape only: Hunyuan's texture stage needs ~16 GB
VRAM, so colour comes from blender/project_texture.py instead.

Licence: Tencent Hunyuan 3D 2.0 Community License (excludes EU/UK/South Korea; >1M MAU needs a
licence from Tencent). Read it before commercial use.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

# Keep the multi-GB weights off the nearly full C: drive.
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[2] / "tools" / "hf_cache"))

import torch  # noqa: E402
from PIL import Image  # noqa: E402

from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover, Hunyuan3DDiTFlowMatchingPipeline  # noqa: E402


def load_view(path: str | None) -> Image.Image | None:
    if not path or not Path(path).exists():
        return None
    img = Image.open(path).convert("RGBA")
    # Square canvas with margin: the model expects the object centred with some breathing room.
    side = int(max(img.size) * 1.15)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True)
    ap.add_argument("--left")
    ap.add_argument("--back")
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--octree", type=int, default=256, help="mesh resolution; 256 fits 8 GB VRAM")
    ap.add_argument("--faces", type=int, default=60000, help="target face count after cleanup")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    views = {k: load_view(getattr(args, k)) for k in ("front", "left", "back")}
    views = {k: v for k, v in views.items() if v is not None}
    print(f"[gen_shape] views: {list(views)}; cuda={torch.cuda.is_available()}", flush=True)

    t0 = time.time()
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", variant="fp16",
    )
    print(f"[gen_shape] model loaded in {time.time() - t0:.0f}s", flush=True)

    t1 = time.time()
    mesh = pipe(
        image=views,
        num_inference_steps=args.steps,
        octree_resolution=args.octree,
        num_chunks=8000,
        generator=torch.manual_seed(args.seed),
        output_type="trimesh",
    )[0]
    print(f"[gen_shape] shape generated in {time.time() - t1:.0f}s: {len(mesh.faces)} faces", flush=True)

    mesh = FloaterRemover()(mesh)
    mesh = DegenerateFaceRemover()(mesh)
    mesh = FaceReducer()(mesh, max_facenum=args.faces)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    mesh.export(args.out)
    print(f"[gen_shape] saved {args.out}: {len(mesh.faces)} faces, {time.time() - t0:.0f}s total", flush=True)


if __name__ == "__main__":
    main()
