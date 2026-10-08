"""Runtime settings, read from environment variables so the same image runs locally or in Docker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    static_dir: Path
    max_upload_bytes: int
    max_image_pixels: int
    rembg_model: str
    rembg_post_process: bool
    alpha_threshold: int
    preload_model: bool
    cache_dir: Path
    renders_dir: Path
    projects_dir: Path
    max_model_bytes: int
    hunyuan_python: Path
    auto_local_3d: bool


settings = Settings(
    static_dir=Path(os.getenv("STATIC_DIR", BASE_DIR / "static")),
    max_upload_bytes=_env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024,
    # 8192 x 8192 is plenty for a full sprite sheet; protects against decompression bombs.
    max_image_pixels=_env_int("MAX_IMAGE_PIXELS", 8192 * 8192),
    # u2net (general), isnet-general-use (sharper edges), isnet-anime (stylised art), u2netp (tiny/fast)
    rembg_model=os.getenv("REMBG_MODEL", "isnet-general-use"),
    # Morphological mask clean-up. Smoother edges, but can eat thin details like swords/staves.
    rembg_post_process=_env_bool("REMBG_POST_PROCESS", False),
    # Pixels with alpha <= this are treated as empty when trimming (kills faint rembg halo noise).
    alpha_threshold=_env_int("ALPHA_THRESHOLD", 8),
    # Load the ONNX model at startup instead of on the first request.
    preload_model=_env_bool("PRELOAD_MODEL", False),
    # Generated texture atlases etc. Safe to delete at any time.
    cache_dir=Path(os.getenv("CACHE_DIR", BASE_DIR / ".cache")),
    # Output folder of blender/render_8dir.py: one sub-folder per character.
    renders_dir=Path(os.getenv("RENDERS_DIR", BASE_DIR / "renders")),
    # Pipeline tab: one folder per character (concept, cutout, profile, uploaded 3D files).
    projects_dir=Path(os.getenv("PROJECTS_DIR", BASE_DIR / "projects")),
    max_model_bytes=_env_int("MAX_MODEL_MB", 500) * 1024 * 1024,
    # Optional local image-to-3D (Hunyuan3D-2mv), installed separately by the user under its own
    # licence (Tencent Hunyuan Community License). Off by default: results were not good enough.
    auto_local_3d=_env_bool("AUTO_LOCAL_3D", False),
    hunyuan_python=Path(os.getenv("HUNYUAN_PYTHON", BASE_DIR.parent / "tools" / "Hunyuan3D-2" / ".venv" / "Scripts" / "python.exe")),
)
