// Common animation model shared by the Sprite Inspector and the Playtest.
// An "animset" (see core/sprite_library.py) = texture atlases + actions (type x direction) + frames
// made of layers. Layer x/y is where the sprite's CENTRE goes relative to the origin (pivot).

import { loadImage } from "../api.js";

export const DIRECTIONS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"];

// Screen-space unit vectors for each direction (+y = down / toward the camera).
const D = Math.SQRT1_2;
export const DIR_VECTORS = {
  S: [0, 1], SW: [-D, D], W: [-1, 0], NW: [-D, -D], N: [0, -1], NE: [D, -D], E: [1, 0], SE: [D, D],
};

/** Nearest of the 8 directions for a screen-space vector. */
export function directionFromVector(dx, dy) {
  // atan2 measured from +y (S) going clockwise on screen: S -> SW -> W ... matches DIRECTIONS.
  const a = Math.atan2(-dx, dy);
  const idx = ((Math.round(a / (Math.PI / 4)) % 8) + 8) % 8;
  return DIRECTIONS[idx];
}

export async function loadAnimSet(data) {
  const atlases = await Promise.all(data.atlases.map((url) => loadImage(url)));
  const byKey = new Map();
  for (const a of data.actions) byKey.set(`${a.type}|${a.dir}`, a);
  return {
    ...data,
    atlasImages: atlases,
    getAction(type, dir) {
      return byKey.get(`${type}|${dir}`) || byKey.get(`${type}|S`) || null;
    },
    hasType(type) {
      return data.action_types.includes(type);
    },
  };
}

/** Duration of one play-through in ms. */
export function actionDuration(action) {
  return action ? action.frames.length * action.delay_ms : 0;
}

/** Frame index at time t (ms). Non-looping actions hold their last frame. */
export function frameIndexAt(action, t, loop = true) {
  if (!action || !action.frames.length) return 0;
  const n = action.frames.length;
  const i = Math.floor(t / Math.max(1, action.delay_ms));
  return loop ? ((i % n) + n) % n : Math.min(i, n - 1);
}

/** First frame carrying an event whose name matches (e.g. "atk"), or -1. */
export function eventFrame(action, name = "atk") {
  if (!action) return -1;
  return action.frames.findIndex((f) => f.event && f.event.toLowerCase() === name);
}

/**
 * Draw one frame with its origin (pivot) at screen (ox, oy).
 * opts: { scale, alpha, layerBoxes, anchors, smoothing, tint }
 */
export function drawFrame(ctx, set, frame, ox, oy, opts = {}) {
  if (!frame) return;
  const scale = opts.scale ?? 1;
  ctx.save();
  ctx.imageSmoothingEnabled = opts.smoothing ?? scale < 1;
  for (const [img, x, y, mirror, sx, sy, rot, alpha] of frame.layers) {
    const rect = set.images[img];
    if (!rect) continue;
    const [page, rx, ry, w, h] = rect;
    ctx.save();
    ctx.translate(ox + x * scale, oy + y * scale);
    if (rot) ctx.rotate((rot * Math.PI) / 180);
    ctx.scale((mirror ? -1 : 1) * sx * scale, sy * scale);
    ctx.globalAlpha = (opts.alpha ?? 1) * (alpha / 255);
    ctx.drawImage(set.atlasImages[page], rx, ry, w, h, -w / 2, -h / 2, w, h);
    ctx.restore();
  }
  ctx.restore();

  if (opts.layerBoxes) {
    ctx.save();
    ctx.lineWidth = 1;
    ctx.font = "10px ui-monospace, monospace";
    frame.layers.forEach(([img, x, y, mirror, sx, sy], li) => {
      const rect = set.images[img];
      if (!rect) return;
      const w = rect[3] * Math.abs(sx) * scale;
      const h = rect[4] * Math.abs(sy) * scale;
      const cx = ox + x * scale;
      const cy = oy + y * scale;
      ctx.strokeStyle = "rgba(79,140,255,0.85)";
      ctx.strokeRect(Math.round(cx - w / 2) + 0.5, Math.round(cy - h / 2) + 0.5, Math.round(w), Math.round(h));
      ctx.fillStyle = "rgba(79,140,255,0.95)";
      ctx.fillText(`L${li}${mirror ? " ↔" : ""}`, cx - w / 2 + 2, cy - h / 2 + 10);
      // layer centre
      ctx.fillRect(cx - 1.5, cy - 1.5, 3, 3);
    });
    ctx.restore();
  }

  if (opts.anchors && frame.anchors.length) {
    ctx.save();
    for (const [x, y] of frame.anchors) {
      ctx.beginPath();
      ctx.arc(ox + x * scale, oy + y * scale, 4, 0, Math.PI * 2);
      ctx.fillStyle = "#f5b84a";
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }
}

/** Bounding box of a frame relative to its origin, in source pixels. */
export function frameBounds(set, frame) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [img, x, y, , sx, sy] of frame?.layers || []) {
    const rect = set.images[img];
    if (!rect) continue;
    const hw = (rect[3] * Math.abs(sx)) / 2;
    const hh = (rect[4] * Math.abs(sy)) / 2;
    x0 = Math.min(x0, x - hw); x1 = Math.max(x1, x + hw);
    y0 = Math.min(y0, y - hh); y1 = Math.max(y1, y + hh);
  }
  return x0 === Infinity ? null : { x0, y0, x1, y1 };
}

/**
 * Bounds over the frames of the given action types - used to size views consistently.
 * Defaults to idle + walk: special actions often carry large effect layers that would
 * otherwise shrink the character to a dot.
 */
export function setBounds(set, types = ["idle", "walk"]) {
  // Rendered characters ship real (visible-pixel) bounds; their layers are padded full frames.
  const known = set.meta?.bounds;
  if (known) {
    let kb = null;
    for (const [type, v] of Object.entries(known)) {
      if (!v || (types.length && !types.includes(type) && Object.keys(known).some((t) => types.includes(t)))) continue;
      kb = kb ? { x0: Math.min(kb.x0, v[0]), y0: Math.min(kb.y0, v[1]), x1: Math.max(kb.x1, v[2]), y1: Math.max(kb.y1, v[3]) }
        : { x0: v[0], y0: v[1], x1: v[2], y1: v[3] };
    }
    if (kb) return kb;
  }
  let b = null;
  const pool = set.actions.filter((a) => types.includes(a.type));
  for (const a of pool.length ? pool : set.actions) {
    for (const f of a.frames) {
      const fb = frameBounds(set, f);
      if (!fb) continue;
      b = b ? { x0: Math.min(b.x0, fb.x0), y0: Math.min(b.y0, fb.y0), x1: Math.max(b.x1, fb.x1), y1: Math.max(b.y1, fb.y1) } : fb;
    }
  }
  return b || { x0: -16, y0: -32, x1: 16, y1: 0 };
}
