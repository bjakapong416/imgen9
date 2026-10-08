// Isometric grid + hitbox shape drawing. All functions draw in whatever transform the ctx already has.

/**
 * Draw an isometric (diamond) grid whose tile centre sits on (ox, oy).
 * Uses diamond coordinates: u = dx/(tw/2) + dy/(th/2), v = dy/(th/2) - dx/(tw/2).
 * A tile centred on the origin has its edges on u = ±1 and v = ±1, so all tile
 * edges are the lines u = odd, v = odd.
 */
export function drawIsoGrid(ctx, { ox, oy, tileW, tileH, bounds, lineWidth, color = "rgba(120,140,180,0.22)", maxLines = 800 }) {
  const hw = tileW / 2;
  const hh = tileH / 2;
  const { x0, y0, x1, y1 } = bounds;
  const corners = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]];
  const us = corners.map(([x, y]) => (x - ox) / hw + (y - oy) / hh);
  const vs = corners.map(([x, y]) => (y - oy) / hh - (x - ox) / hw);
  const uMin = Math.min(...us), uMax = Math.max(...us);
  const vMin = Math.min(...vs), vMax = Math.max(...vs);
  if ((uMax - uMin) / 2 + (vMax - vMin) / 2 > maxLines) return; // too dense to be useful

  ctx.beginPath();
  // u = c  ->  y = oy + (c - (x-ox)/hw) * hh
  for (let k = Math.ceil((uMin - 1) / 2); 2 * k + 1 <= uMax; k++) {
    const c = 2 * k + 1;
    ctx.moveTo(x0, oy + (c - (x0 - ox) / hw) * hh);
    ctx.lineTo(x1, oy + (c - (x1 - ox) / hw) * hh);
  }
  // v = c  ->  y = oy + (c + (x-ox)/hw) * hh
  for (let k = Math.ceil((vMin - 1) / 2); 2 * k + 1 <= vMax; k++) {
    const c = 2 * k + 1;
    ctx.moveTo(x0, oy + (c + (x0 - ox) / hw) * hh);
    ctx.lineTo(x1, oy + (c + (x1 - ox) / hw) * hh);
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.stroke();
}

/** Diamond path for the single tile centred on (cx, cy). */
export function tilePath(ctx, cx, cy, tileW, tileH) {
  ctx.beginPath();
  ctx.moveTo(cx, cy - tileH / 2);
  ctx.lineTo(cx + tileW / 2, cy);
  ctx.lineTo(cx, cy + tileH / 2);
  ctx.lineTo(cx - tileW / 2, cy);
  ctx.closePath();
}

/**
 * Draw a hitbox. Geometry is { shape, cx, by, w, h } where (cx, by) is the bottom-centre.
 * A cylinder's base ellipse is centred on (cx, by) and flattened by the tile aspect ratio,
 * so it sits on the isometric ground plane.
 */
export function drawHitbox(ctx, hb, { tileW, tileH, lineWidth, stroke, fill, dashed = false }) {
  const { cx, by, w, h } = hb;
  ctx.save();
  ctx.lineWidth = lineWidth;
  ctx.strokeStyle = stroke;
  ctx.fillStyle = fill;
  if (dashed) ctx.setLineDash([4 * lineWidth, 3 * lineWidth]);

  if (hb.shape === "cylinder") {
    const rx = Math.max(w / 2, 0.01);
    const ry = rx * (tileH / tileW);
    const ty = by - h;

    // Silhouette: left side, front half of base, right side, back half of top.
    ctx.beginPath();
    ctx.moveTo(cx - rx, ty);
    ctx.lineTo(cx - rx, by);
    ctx.ellipse(cx, by, rx, ry, 0, Math.PI, 0, true);
    ctx.lineTo(cx + rx, ty);
    ctx.ellipse(cx, ty, rx, ry, 0, 0, Math.PI, true);
    ctx.closePath();
    if (fill) ctx.fill();

    ctx.beginPath(); // top cap
    ctx.ellipse(cx, ty, rx, ry, 0, 0, Math.PI * 2);
    ctx.moveTo(cx - rx, ty); ctx.lineTo(cx - rx, by);
    ctx.moveTo(cx + rx, ty); ctx.lineTo(cx + rx, by);
    ctx.stroke();

    ctx.beginPath(); // front of base
    ctx.ellipse(cx, by, rx, ry, 0, 0, Math.PI);
    ctx.stroke();

    ctx.save(); // hidden back of base
    ctx.globalAlpha *= 0.5;
    ctx.setLineDash([3 * lineWidth, 3 * lineWidth]);
    ctx.beginPath();
    ctx.ellipse(cx, by, rx, ry, 0, Math.PI, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  } else {
    ctx.beginPath();
    ctx.rect(cx - w / 2, by - h, w, h);
    if (fill) ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

/** Screen-space crosshair marker for the pivot. */
export function drawPivotMarker(ctx, x, y, { color = "#ff3b4f", size = 14, ghost = false } = {}) {
  ctx.save();
  ctx.lineCap = "round";
  if (ghost) {
    ctx.globalAlpha = 0.55;
    size = 5;
  }
  const lines = () => {
    ctx.beginPath();
    ctx.moveTo(x - size, y); ctx.lineTo(x - 3, y);
    ctx.moveTo(x + 3, y); ctx.lineTo(x + size, y);
    ctx.moveTo(x, y - size); ctx.lineTo(x, y - 3);
    ctx.moveTo(x, y + 3); ctx.lineTo(x, y + size);
    if (!ghost) {
      ctx.moveTo(x + size * 0.6, y);
      ctx.arc(x, y, size * 0.6, 0, Math.PI * 2);
    }
  };
  ctx.strokeStyle = "rgba(0,0,0,0.8)";
  ctx.lineWidth = ghost ? 3 : 4;
  lines(); ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = ghost ? 1.25 : 2;
  lines(); ctx.stroke();
  if (!ghost) {
    ctx.beginPath();
    ctx.arc(x, y, 1.75, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }
  ctx.restore();
}
