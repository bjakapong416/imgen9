"""
ImGen9 (character & monster sprite maker) - FastAPI backend.

Run:  uvicorn main:app --reload
Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from core import connections, i18n
from core.i18n import _
from core.characters import CharacterError, CharacterLibrary
from core.config import settings
from core.version import RELEASES_URL, VERSION
from core.classify import claude_available
from core.image_processing import ImageProcessingError, get_rembg_session
from core.projects import ProjectError, ProjectStore, find_blender
from core.sprite_library import SpriteError, SpriteLibrary

log = logging.getLogger("character_setup")

UPLOAD_CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.preload_model:
        log.info("Preloading rembg model '%s'...", settings.rembg_model)
        await run_in_threadpool(get_rembg_session, settings.rembg_model)
    yield


app = FastAPI(title="ImGen9", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def language(request, call_next):
    """Text the server writes follows the UI language (cookie set by static/js/i18n.js)."""
    token = i18n.set_lang(i18n.pick(request.cookies.get("lang"), request.headers.get("accept-language")))
    try:
        return await call_next(request)
    finally:
        i18n.reset(token)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """The UI changes often: make browsers revalidate HTML/JS/CSS instead of using a stale copy."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Make sure mount targets exist before StaticFiles checks them at import time.
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
settings.renders_dir.mkdir(parents=True, exist_ok=True)
settings.projects_dir.mkdir(parents=True, exist_ok=True)


sprite_lib = SpriteLibrary(settings.renders_dir, settings.cache_dir / "sprites")
characters = CharacterLibrary(settings.renders_dir)
projects = ProjectStore(settings.projects_dir, settings.renders_dir, settings)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


def _inside(base: Path, rel: str) -> Path:
    """base/rel, refusing anything that would leave base."""
    target = (base / rel).resolve()
    if base.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "Not found.")
    return target


# Character files: /projects/<character>/... and /renders/<character>/... (its project is looked up).
@app.get("/projects/{slug}/{rel:path}", include_in_schema=False)
async def project_file(slug: str, rel: str) -> FileResponse:
    try:
        base = projects.layout.project_dir(slug)
    except ValueError:
        raise HTTPException(404, "Not found.") from None
    return FileResponse(_inside(base, rel))


@app.get("/renders/{slug}/{rel:path}", include_in_schema=False)
async def render_file(slug: str, rel: str) -> FileResponse:
    return FileResponse(_inside(projects.layout.render_dir(slug), rel))


@app.get("/groups/{gid}/style.png", include_in_schema=False)
async def group_style_image(gid: str) -> FileResponse:
    try:
        base = projects.layout.group_dir(gid)
    except ValueError:
        raise HTTPException(404, "Not found.") from None
    return FileResponse(_inside(base, "style.png"))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": VERSION, "rembg_model": settings.rembg_model}


@app.get("/api/version")
async def version() -> dict:
    """The running version and where to get newer ones (the app itself never checks online)."""
    return {"version": VERSION, "releases": RELEASES_URL}


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read an upload in chunks, refusing anything over the size limit."""
    buf = bytearray()
    while chunk := await file.read(UPLOAD_CHUNK):
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(413, f"File exceeds {limit // (1024 * 1024)} MB limit.")
    if not buf:
        raise HTTPException(400, "Uploaded file is empty.")
    return bytes(buf)


def _atlas_url(sprite_id: str, page: int) -> str:
    return f"/api/sprites/atlas?id={sprite_id}&page={page}"


@app.get("/api/sprites/files")
async def sprite_files(q: str = "", limit: int = Query(300, ge=1, le=5000), project: str | None = None) -> dict:
    result = await run_in_threadpool(sprite_lib.search, q, limit, project)
    result["available"] = sprite_lib.available
    return result


@app.get("/api/sprites/animset")
async def sprite_animset(id: str) -> dict:
    try:
        return await run_in_threadpool(sprite_lib.animset, id, _atlas_url)
    except SpriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        log.exception("Failed to read sprite %s", id)
        raise HTTPException(422, f"Could not read sprite: {exc}") from exc


@app.get("/api/sprites/atlas")
async def sprite_atlas(id: str, page: int = 0) -> FileResponse:
    try:
        file = await run_in_threadpool(sprite_lib.atlas_page, id, page)
    except SpriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(file, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


_demo_cache: dict[tuple[str, int], dict[str, bytes]] = {}


@app.get("/api/sprites/demo/{id}/{file}")
async def sprite_web_demo(id: str, file: str) -> Response:
    """The web-game bundle served as a folder, so its demo.html runs right here: browsers won't run it
    from a downloaded zip (file:// can't fetch the atlas) without a local web server."""
    from core import sprite_images
    from core.spr_act import read_act, read_spr
    from core.web_bundle import build as web_build

    def files() -> dict[str, bytes]:
        act_path = sprite_lib.resolve(id)
        key = (id, act_path.stat().st_mtime_ns)
        if key not in _demo_cache:
            act, spr = read_act(act_path.read_bytes()), read_spr(act_path.with_suffix(".spr").read_bytes())
            _demo_cache.clear()                     # one character at a time is plenty
            _demo_cache[key] = web_build(act_path.stem, act, spr, sprite_images.type_names_for(act_path))
        return _demo_cache[key]

    try:
        bundle = await run_in_threadpool(files)
    except SpriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    if file not in bundle:
        raise HTTPException(404, "Not in this bundle.")
    media = {".html": "text/html; charset=utf-8", ".json": "application/json", ".png": "image/png",
             ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}.get("." + file.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(bundle[file], media_type=media, headers={"Cache-Control": "no-store"})


@app.get("/api/sprites/download")
async def sprite_download(id: str, kind: str = Query("zip", pattern="^(frame|strip|gif|zip|web|godot|aseprite)$"),
                      action: int = 0, frame: int = 0) -> Response:
    """Images from one of the sprites made here (frame PNG / strip PNG / GIF / everything zipped)."""
    from core import sprite_images
    from core.spr_act import read_act, read_spr

    def build():
        act_path = sprite_lib.resolve(id)
        act_bytes = act_path.read_bytes()
        spr_bytes = act_path.with_suffix(".spr").read_bytes()
        act, spr = read_act(act_bytes), read_spr(spr_bytes)
        name = act_path.stem
        names = sprite_images.type_names_for(act_path)
        if kind == "zip":
            return sprite_images.export_zip(name, act, spr, act_bytes, spr_bytes, names), "application/zip", f"{name}.zip"
        if kind in ("web", "godot", "aseprite"):  # game-ready bundles: one atlas, files per engine
            import io as _io
            import zipfile as _zf

            from core import engine_export
            from core.web_bundle import build as web_build, layout
            atlas = layout(name, act, spr, names)
            folder = f"{name}_{kind}"
            if kind == "web":   # atlas + json + demo, plus the Godot and Aseprite files
                files = {**web_build(name, act, spr, names, atlas=atlas),
                         **engine_export.godot_files(atlas, folder), **engine_export.aseprite_files(atlas)}
            elif kind == "godot":
                files = engine_export.godot_bundle(atlas)
            else:
                files = engine_export.aseprite_bundle(atlas)
            game = act_path.parent / "game.json"   # hitbox, shadow, cast points (2D characters)
            if game.exists():
                files["game.json"] = game.read_bytes()
            buf = _io.BytesIO()
            with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as z:
                for fname, data in files.items():
                    z.writestr(f"{folder}/{fname}", data)
            return buf.getvalue(), "application/zip", f"{folder}.zip"
        if not 0 <= action < len(act.actions) or not act.actions[action].frames:
            raise SpriteError("No such action.")
        canvas = sprite_images.canvas_for(act, spr)
        a = act.actions[action]
        t, d = sprite_images.action_label(act, action, names)
        if kind == "frame":
            f = a.frames[max(0, min(frame, len(a.frames) - 1))]
            return sprite_images.png_bytes(sprite_images.render_frame(act, spr, f, canvas)), "image/png", f"{name}_{t}_{d}_{frame:02d}.png"
        frames = [sprite_images.render_frame(act, spr, f, canvas) for f in a.frames]
        if kind == "strip":
            return sprite_images.strip_png(frames), "image/png", f"{name}_{t}_{d}_strip.png"
        return sprite_images.gif_bytes(frames, a.delay_ms), "image/gif", f"{name}_{t}_{d}.gif"

    try:
        data, media, filename = await run_in_threadpool(build)
    except SpriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ───────────────────────── rendered characters (Playtest) ─────────────────────────

@app.get("/api/characters")
async def list_characters() -> dict:
    items = await run_in_threadpool(characters.list)
    return {"root": str(settings.renders_dir), "items": items}


@app.get("/api/characters/{name}")
async def get_character(name: str) -> dict:
    try:
        return await run_in_threadpool(characters.animset, name)
    except CharacterError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/characters/{name}/lab")
async def load_character_lab(name: str) -> dict:
    try:
        return await run_in_threadpool(characters.load_lab, name)
    except CharacterError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/characters/{name}/lab")
async def save_character_lab(name: str, config: dict = Body(...)) -> dict:
    try:
        return await run_in_threadpool(characters.save_lab, name, config)
    except CharacterError as exc:
        raise HTTPException(400, str(exc)) from exc


# ───────────────────────── Pipeline: one image -> game-ready character ─────────────────────────

def _project_call(fn, *args):
    try:
        return fn(*args)
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ImageProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/pipeline/capabilities")
async def pipeline_capabilities() -> dict:
    conn = await run_in_threadpool(connections.status)
    return {"claude": claude_available(), "blender": find_blender(),
            "claude_code": conn["tools"]["claude_code"]["found"],
            "assistant": any(t["found"] for t in conn["tools"].values())}


@app.get("/api/connections")
async def connections_status(refresh: bool = False) -> dict:
    """AI tools on this PC (command-line tools on PATH, API keys set or not). Checked locally only."""
    return await run_in_threadpool(connections.status, refresh)


@app.get("/api/projects")
async def list_projects() -> dict:
    return {"items": await run_in_threadpool(projects.list)}


@app.post("/api/projects")
async def create_project(
    file: UploadFile = File(...),
    name: str = Form(""),
    method: str = Form("heuristic", pattern="^(auto|claude|claude_code|heuristic)$"),
    group: str = Form(""),
) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    try:
        return await run_in_threadpool(_project_call, projects.create, data, file.filename or "", name, method, group)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Project creation failed")
        raise HTTPException(500, f"Could not create project: {exc}") from exc


@app.get("/api/projects/{slug}")
async def get_project(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.get, slug)


@app.put("/api/projects/{slug}")
async def update_project(slug: str, patch: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.update, slug, patch)


@app.post("/api/projects/{slug}/classify")
async def reclassify_project(slug: str, method: str = Query("claude_code", pattern="^(auto|claude|claude_code|heuristic)$")) -> dict:
    try:
        return await run_in_threadpool(_project_call, projects.reclassify, slug, method)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Classification failed: {exc}") from exc


@app.post("/api/projects/{slug}/views/{view}")
async def set_project_view(slug: str, view: str, file: UploadFile = File(...)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.set_view, slug, view, data)


@app.delete("/api/projects/{slug}/views/{view}")
async def delete_project_view(slug: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.remove_view, slug, view)


@app.post("/api/projects/{slug}/views-split")
async def split_project_sheet(slug: str, file: UploadFile = File(...)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.split_sheet, slug, data)


@app.post("/api/projects/{slug}/views-assign")
async def assign_project_views(slug: str, assignment: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.assign_pieces, slug, assignment)


@app.post("/api/projects/{slug}/sheets/{action}/{view}")
async def add_project_sheet(slug: str, action: str, view: str, file: UploadFile = File(...)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.add_sheet, slug, action, view, data)


@app.post("/api/projects/{slug}/sheets/{action}/{view}/flip")
async def flip_project_sheet(slug: str, action: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.flip_sheet, slug, action, view)


@app.delete("/api/projects/{slug}/sheets/{action}/{view}")
async def remove_project_sheet(slug: str, action: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.remove_sheet, slug, action, view)


@app.post("/api/projects/{slug}/weapons")
async def add_project_weapon(slug: str, file: UploadFile = File(...), name: str = Form(""), type: str = Form("")) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.add_weapon, slug, data, name, type)


@app.post("/api/projects/{slug}/weapons/sample")
async def add_project_sample_weapon(slug: str, type: str) -> dict:
    return await run_in_threadpool(_project_call, projects.add_sample_weapon, slug, type)


@app.post("/api/projects/{slug}/weapons/from-extra")
async def project_weapon_from_extra(slug: str, extra: str) -> dict:
    return await run_in_threadpool(_project_call, projects.weapon_from_extra, slug, extra)


@app.put("/api/projects/{slug}/weapons")
async def update_project_weapons(slug: str, patch: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.update_weapons, slug, patch)


@app.delete("/api/projects/{slug}/weapons/{wid}")
async def remove_project_weapon(slug: str, wid: str) -> dict:
    return await run_in_threadpool(_project_call, projects.remove_weapon, slug, wid)


@app.get("/api/projects/{slug}/weapon-frames")
async def project_weapon_frames(slug: str, action: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.weapon_frames, slug, action, view)


@app.get("/api/projects/{slug}/strip")
async def project_strip(slug: str, action: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.strip_frames, slug, action, view)


@app.put("/api/projects/{slug}/strip")
async def save_project_strip(slug: str, action: str, view: str, body: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.save_frame_edits, slug, action, view, body.get("edits"))


@app.post("/api/projects/{slug}/strip/frame")
async def add_project_strip_frame(slug: str, action: str, view: str, file: UploadFile = File(...)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.add_frame_image, slug, action, view, data)


@app.post("/api/projects/{slug}/sheets/{action}/{view}/undo")
async def undo_project_sheet(slug: str, action: str, view: str) -> dict:
    return await run_in_threadpool(_project_call, projects.undo_sheet, slug, action, view)


@app.get("/api/projects/{slug}/reference-board")
async def project_reference_board(slug: str, action: str = "all", view: str = "both") -> Response:
    data = await run_in_threadpool(_project_call, projects.reference_board, slug, action, view)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.put("/api/projects/{slug}/variants")
async def save_project_variants(slug: str, body: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.save_variants, slug, body.get("variants") or [])


@app.get("/api/projects/{slug}/variant-preview")
async def project_variant_preview(slug: str, hue_from: float = 0, hue_to: float = 360, shift: float = 0,
                                  sat: float = 1, light: float = 1) -> Response:
    rule = {"hue_from": hue_from, "hue_to": hue_to, "shift": shift, "sat": sat, "light": light}
    data = await run_in_threadpool(_project_call, projects.variant_preview, slug, rule)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{slug}/hats")
async def add_project_hat(slug: str, file: UploadFile = File(...), name: str = Form("")) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.add_hat, slug, data, name)


@app.put("/api/projects/{slug}/hats")
async def update_project_hats(slug: str, patch: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.update_hats, slug, patch)


@app.delete("/api/projects/{slug}/hats/{hid}")
async def remove_project_hat(slug: str, hid: str) -> dict:
    return await run_in_threadpool(_project_call, projects.remove_hat, slug, hid)


@app.post("/api/projects/{slug}/mounted")
async def make_project_mounted(slug: str, body: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.make_mounted, slug, body.get("mount", ""))


@app.get("/api/projects/{slug}/pose-guide")
async def project_pose_guide(slug: str, action: str = "all", view: str = "both") -> Response:
    data = await run_in_threadpool(_project_call, projects.pose_guide, slug, action, view)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{slug}/build2d")
async def build_project_2d(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.build_2d, slug)


@app.post("/api/projects/{slug}/views-flip")
async def flip_project_view(slug: str, view: str = Query(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.flip_view, slug, view)


@app.post("/api/projects/{slug}/spr-export")
async def project_spr_export(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.export_spr, slug)


@app.post("/api/projects/{slug}/gen3d")
async def start_project_gen3d(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.start_gen3d, slug)


@app.post("/api/projects/{slug}/views-swap")
async def swap_project_views(slug: str, a: str = Query(...), b: str = Query(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.swap_views, slug, a, b)


@app.post("/api/projects/{slug}/files")
async def upload_project_files(slug: str, files: list[UploadFile] = File(...)) -> dict:
    result = None
    for f in files:
        result = await run_in_threadpool(_project_call, projects.add_model_file, slug, f.filename, f.file, settings.max_model_bytes)
    return result


@app.delete("/api/projects/{slug}/files/{filename}")
async def delete_project_file(slug: str, filename: str) -> dict:
    return await run_in_threadpool(_project_call, projects.remove_model_file, slug, filename)


@app.post("/api/projects/{slug}/render")
async def start_project_render(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.start_render, slug)


@app.get("/api/projects/{slug}/render")
async def project_render_status(slug: str) -> dict:
    return {"job": projects.job_status(slug, "render")}


@app.post("/api/projects/{slug}/inspect")
async def start_project_inspect(slug: str) -> dict:
    return await run_in_threadpool(_project_call, projects.start_inspect, slug)


@app.get("/api/projects/{slug}/jobs")
async def project_jobs(slug: str) -> dict:
    return {kind: projects.job_status(slug, kind) for kind in ("gen3d", "inspect", "render")}


@app.get("/api/projects/{slug}/render-command")
async def project_render_command(slug: str) -> dict:
    cmd = await run_in_threadpool(_project_call, projects.render_command, slug)
    return {"command": " ".join(f'"{c}"' if " " in c else c for c in cmd)}


@app.get("/api/projects/{slug}/export.zip")
async def export_project(slug: str) -> Response:
    data = await run_in_threadpool(_project_call, projects.export_zip, slug)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


# ───────────────────────── projects (groups of characters) ─────────────────────────

@app.get("/api/groups")
async def list_groups() -> dict:
    return {"items": await run_in_threadpool(projects.list_groups)}


@app.post("/api/groups")
async def create_group(body: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.create_group, str(body.get("name") or ""))


@app.get("/api/groups/{gid}")
async def get_group(gid: str) -> dict:
    return await run_in_threadpool(_project_call, projects.get_group, gid)


@app.put("/api/groups/{gid}")
async def update_group(gid: str, patch: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.update_group, gid, patch)


@app.delete("/api/groups/{gid}")
async def delete_group(gid: str) -> dict:
    return await run_in_threadpool(_project_call, projects.delete_group, gid)


@app.post("/api/groups/{gid}/style-image")
async def set_group_style_image(gid: str, file: UploadFile = File(...)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.set_group_style_image, gid, data)


@app.delete("/api/groups/{gid}/style-image")
async def clear_group_style_image(gid: str) -> dict:
    return await run_in_threadpool(_project_call, projects.set_group_style_image, gid, None)


@app.get("/api/groups/{gid}/download.zip")
async def download_group(gid: str) -> Response:
    data, name = await run_in_threadpool(_project_call, projects.export_group_zip, gid)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/projects/{slug}/move")
async def move_project(slug: str, body: dict = Body(...)) -> dict:
    return await run_in_threadpool(_project_call, projects.move_character, slug, str(body.get("group") or ""))


@app.post("/api/projects/{slug}/concept")
async def replace_concept(slug: str, file: UploadFile = File(...), reclassify: bool = Form(False)) -> dict:
    data = await _read_limited(file, settings.max_upload_bytes)
    return await run_in_threadpool(_project_call, projects.replace_concept, slug, data, reclassify)
