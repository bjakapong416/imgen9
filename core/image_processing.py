"""Background removal (rembg) and transparent-space trimming (Pillow). Pure functions, no web concerns."""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageProcessingError(ValueError):
    """Raised for user-facing problems with the uploaded image."""


@dataclass
class ProcessResult:
    image: Image.Image
    original_size: tuple[int, int]
    # (x, y, width, height) of the kept region in original-image pixels, or None if not trimmed.
    trim_box: tuple[int, int, int, int] | None
    background_removed: bool


_session = None
_session_model: str | None = None
_session_lock = threading.Lock()


def get_rembg_session(model: str):
    """Create the rembg ONNX session once and reuse it (loading it is the slow part)."""
    global _session, _session_model
    if _session is None or _session_model != model:
        with _session_lock:
            if _session is None or _session_model != model:
                from rembg import new_session  # heavy import, keep it lazy

                _session = new_session(model)
                _session_model = model
    return _session


def load_image(data: bytes, max_pixels: int) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))  # lazy: only the header is parsed here
    except UnidentifiedImageError as exc:
        raise ImageProcessingError("File is not a supported image (PNG, JPG, WEBP, ...).") from exc

    w, h = img.size
    if w * h > max_pixels:
        raise ImageProcessingError(f"Image is too large ({w}x{h}). Limit is {max_pixels:,} pixels.")

    try:
        img.load()
    except Exception as exc:  # truncated / corrupt files
        raise ImageProcessingError(f"Could not decode image: {exc}") from exc

    img = ImageOps.exif_transpose(img)  # respect camera/phone rotation flags
    return img.convert("RGBA")


def remove_background(img: Image.Image, model: str, post_process: bool) -> Image.Image:
    from rembg import remove

    out = remove(img, session=get_rembg_session(model), post_process_mask=post_process)
    return out.convert("RGBA")


def trim_transparent(
    img: Image.Image, alpha_threshold: int, padding: int = 0
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop to the bounding box of visible pixels. Returns the image and its (x, y, w, h) crop box."""
    alpha = img.getchannel("A")
    # Zero out near-invisible noise so it neither shows up in-engine nor inflates the bounds.
    cleaned = alpha.point(lambda a: 0 if a <= alpha_threshold else a)
    img = img.copy()
    img.putalpha(cleaned)

    bbox = cleaned.getbbox()
    if bbox is None:
        raise ImageProcessingError("Image is fully transparent - nothing left after background removal.")

    left, top, right, bottom = bbox
    if padding:
        left = max(0, left - padding)
        top = max(0, top - padding)
        right = min(img.width, right + padding)
        bottom = min(img.height, bottom + padding)

    return img.crop((left, top, right, bottom)), (left, top, right - left, bottom - top)


def process_character(
    data: bytes,
    *,
    remove_bg: bool,
    trim: bool,
    padding: int,
    model: str,
    post_process: bool,
    alpha_threshold: int,
    max_pixels: int,
) -> ProcessResult:
    img = load_image(data, max_pixels)
    original_size = img.size

    if remove_bg:
        img = remove_background(img, model, post_process)

    trim_box = None
    if trim:
        img, trim_box = trim_transparent(img, alpha_threshold, padding)

    return ProcessResult(
        image=img,
        original_size=original_size,
        trim_box=trim_box,
        background_removed=remove_bg,
    )
