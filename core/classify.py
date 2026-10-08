"""
Classifies one character / creature image into a game profile.

Two methods:
  * "claude"    - Claude vision with a structured-output schema (needs ANTHROPIC_API_KEY or an
                  `ant auth login` profile). Reads the art like a designer would: body type,
                  element, breed, features, which animations and skills it needs.
  * "heuristic" - no AI, no network. Colour analysis for the element and silhouette shape for the
                  body type. Rough: every field it fills is marked low-confidence for the user to confirm.
Both return the same dict shape (CreatureProfile).
"""

from __future__ import annotations

import base64
import colorsys
import io
import logging
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from .i18n import _

log = logging.getLogger("character_setup.classify")

CLAUDE_MODEL = "claude-opus-5-5"

Category = Literal["player", "monster", "pet", "npc", "boss"]
BodyType = Literal["humanoid", "quadruped", "winged_quadruped", "bird", "serpent", "blob", "insect", "other"]
Element = Literal["fire", "water", "ice", "earth", "wind", "lightning", "nature", "dark", "holy", "poison", "neutral"]
Rank = Literal["S", "A", "B", "C", "D"]
ArtStyle = Literal["chibi_anime", "pixel_art", "painterly", "realistic", "3d_render", "other"]
View = Literal["front", "three_quarter", "side", "back", "multiple"]


class AnimationPlan(BaseModel):
    key: str = Field(description="short id: idle, walk, run, attack, hurt, die, sit, sleep, happy, cast, ...")
    label_th: str = Field(description="Thai label, e.g. ยืน, เดิน, โจมตี")
    reason: str = Field(description="why this character needs it (Thai, one short sentence)")


class SkillIdea(BaseModel):
    name_th: str
    description_th: str
    vfx: str = Field(description="visual effect needed, e.g. 'fire trail', 'ground shockwave'")


class CreatureProfile(BaseModel):
    name_th: str
    name_en: str = Field(description="snake_case English id, e.g. flame_horn_beast")
    category: Category
    body_type: BodyType
    breed: str = Field(description="species / hybrid description in Thai, e.g. หมาวัว (ผสม)")
    element: Element
    rank: Rank
    features: list[str] = Field(description="3-5 distinctive visual features in Thai")
    art_style: ArtStyle
    view: View = Field(description="camera view of the character in the image")
    is_sprite_sheet: bool = Field(description="true if the image is a grid of animation frames or a multi-panel sheet")
    height_m: float = Field(description="plausible in-world height in metres")
    animations: list[AnimationPlan]
    skills: list[SkillIdea]
    notes: str = Field(description="production notes in Thai: rigging path, risks, things to watch")
    description_en: str = Field(
        default="",
        description="one English sentence describing the character's look for image AIs "
                    "(hair, outfit, colours, notable items), used in every sprite prompt")
    turnaround_prompt: str = Field(
        default="",
        description="English image-generation prompt for a front / 3/4 / side / back turnaround of this exact "
                    "character (pose suited to rigging, plain background), used when views are missing")


SYSTEM_PROMPT = """You are the lead technical artist of a 2.5D isometric web MMORPG with classic hand-drawn sprite art.
You receive one concept image of a character or creature that will become an 8-direction animated sprite.
Fill the profile so the production pipeline can start:
- body_type decides the rig: humanoid -> Mixamo auto-rig; anything else -> custom rig in Blender.
- animations: always include idle, walk, attack, hurt, die. Add extras only when the character's role
  calls for them (a pet: sit, sleep, happy; a caster: cast; a boss: a second attack).
- skills: 2-4 ideas that fit the element and body. Describe the VFX each one needs.
- Thai text for names, breed, features, reasons and notes; English snake_case for name_en.
Judge only from the image. If something is unclear, make the most plausible call and say so in notes."""


def claude_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    import os

    # The SDK can also use an `ant auth login` profile; we can't cheaply probe that, so an env var
    # or an explicit opt-in is required before we try (and spend money).
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("CLASSIFY_WITH_CLAUDE"))


def classify_with_claude(image: Image.Image) -> dict:
    import anthropic

    img = image.copy()
    img.thumbnail((1568, 1568))  # larger images are downscaled by the API anyway
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.standard_b64encode(buf.getvalue()).decode("ascii")

    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # re-run on a fallback model if the request is declined
        output_config={"effort": "medium"},
        output_format=CreatureProfile,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                {"type": "text", "text": "Classify this character for the pipeline."},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) if response.stop_details else None
        raise RuntimeError(f"Claude declined to classify this image{': ' + detail if detail else ''}.")
    profile = response.parsed_output
    if profile is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason}).")
    out = profile.model_dump()
    out["_method"] = "claude"
    out["_model"] = response.model
    out["_confidence"] = {}
    return out


# ───────────────────────────── heuristic fallback ─────────────────────────────

ELEMENT_HUES = {
    # element: (hue ranges in degrees, min saturation, min value)
    "fire": ([(0, 40), (345, 360)], 0.45, 0.45),
    "lightning": ([(45, 65)], 0.45, 0.65),
    "nature": ([(70, 160)], 0.30, 0.25),
    "water": ([(185, 240)], 0.35, 0.30),
    "ice": ([(170, 210)], 0.10, 0.70),
    "dark": ([(255, 320)], 0.25, 0.10),
    "poison": ([(280, 330)], 0.40, 0.30),
}

# Labels and reasons are English; classify_heuristic translates them (inside the request) with _().
DEFAULT_ANIMS = [
    ("idle", "Idle", "Basic resting pose"),
    ("walk", "Walk", "Moves in 8 directions"),
    ("attack", "Attack", "Normal attack"),
    ("hurt", "Hurt", "Reacts to being hit (.act slot: damage)"),
    ("die", "Die", "Death pose"),
]
PET_ANIMS = [("sit", "Sit", "A pet's resting pose"), ("sleep", "Sleep", "Long rest pose"), ("happy", "Happy", "Reacts to the player")]


def palette(image: Image.Image, n: int = 6) -> list[str]:
    rgba = image.convert("RGBA")
    rgba.thumbnail((160, 160))
    # Composite on black and drop transparent pixels by sampling only opaque ones.
    px = [p[:3] for p in rgba.getdata() if p[3] > 128]
    if not px:
        return []
    tmp = Image.new("RGB", (len(px), 1))
    tmp.putdata(px)
    q = tmp.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: n * 3]
    counts = sorted(q.getcolors(), reverse=True)
    return ["#%02x%02x%02x" % tuple(pal[i * 3:i * 3 + 3]) for _, i in counts]


def element_scores(image: Image.Image) -> dict[str, float]:
    rgba = image.convert("RGBA")
    rgba.thumbnail((200, 200))
    scores = {k: 0.0 for k in ELEMENT_HUES}
    total = 0
    dark = bright = 0
    for r, g, b, a in rgba.getdata():
        if a < 128:
            continue
        total += 1
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        deg = h * 360
        if v < 0.18:
            dark += 1
        if v > 0.85 and s < 0.25:
            bright += 1
        for el, (ranges, smin, vmin) in ELEMENT_HUES.items():
            if s >= smin and v >= vmin and any(lo <= deg < hi for lo, hi in ranges):
                scores[el] += 1
    if not total:
        return {}
    out = {k: v / total for k, v in scores.items()}
    out["dark"] = max(out["dark"], dark / total * 0.8)
    out["holy"] = bright / total * 0.8
    return out


def classify_heuristic(image: Image.Image, name_hint: str = "") -> dict:
    bbox = image.getchannel("A").getbbox() if image.mode == "RGBA" else None
    w, h = (bbox[2] - bbox[0], bbox[3] - bbox[1]) if bbox else image.size
    aspect = w / max(1, h)
    if aspect < 0.62:
        body, body_conf = "humanoid", 0.6
    elif aspect > 1.2:
        body, body_conf = "quadruped", 0.55
    else:
        body, body_conf = "other", 0.2  # square silhouettes: crouching beasts, blobs, chibi bipeds...

    scores = element_scores(image)
    element, el_score = max(scores.items(), key=lambda kv: kv[1]) if scores else ("neutral", 0.0)
    if el_score < 0.15:
        element, el_conf = "neutral", 0.3
    else:
        el_conf = min(0.9, 0.35 + el_score)

    slug = "".join(c if c.isalnum() else "_" for c in name_hint.lower()).strip("_") or "character"
    anims = DEFAULT_ANIMS + (PET_ANIMS if body != "humanoid" else [])
    return {
        "name_th": name_hint or _("New character"),
        "name_en": slug,
        "category": "monster",
        "body_type": body,
        "breed": "",
        "element": element,
        "rank": "B",
        "features": [],
        "art_style": "other",
        "view": "front",
        "is_sprite_sheet": False,
        "height_m": 1.7 if body == "humanoid" else 1.4,
        "animations": [{"key": k, "label_th": _(t), "reason": _(r)} for k, t, r in anims],
        "skills": [],
        "notes": _("Classified without AI (colour and shape): the values are guesses, please check and fix them"),
        "_method": "heuristic",
        "_model": None,
        "_confidence": {"body_type": round(body_conf, 2), "element": round(el_conf, 2), "aspect": round(aspect, 2)},
    }


def classify(image: Image.Image, name_hint: str = "", method: str = "auto") -> dict:
    """method: auto | claude | heuristic. auto uses Claude when credentials exist."""
    result = None
    error = None
    if method in ("auto", "claude") and (method == "claude" or claude_available()):
        try:
            result = classify_with_claude(image)
        except Exception as exc:  # network, auth, refusal... fall back so the pipeline keeps going
            log.warning("Claude classification failed: %s", exc)
            error = str(exc)
            if method == "claude":
                raise
    if result is None:
        result = classify_heuristic(image, name_hint)
        if error:
            result["notes"] += _(" (Claude unavailable: {error})", error=error)
    result["palette"] = palette(image)
    return result
