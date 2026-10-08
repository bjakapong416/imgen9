// Playtest scene: actors (rendered characters or .spr/.act sprites) on an isometric ground plane.
//
// World units are metres on the ground: x = screen-right, y = toward the camera (screen-down).
// Screen: sx = W/2 + (x - camX) * P * zoom,  sy = H/2 + (y - camY) * P * zoom * sin(elevation)
// where P = scene pixels per metre. Each actor's sprite is scaled by P * zoom / actor.ppm.

import { actionDuration, directionFromVector, DIR_VECTORS, drawFrame, eventFrame, frameIndexAt, setBounds } from "../anim/animset.js";
import { drawIsoGrid, tilePath } from "../iso.js";

const P = 64;                 // scene pixels per metre at zoom 1
const DEFAULT_HIT_FRACTION = 0.59; // the usual hit point: 59% into the attack
const PALETTE = ["#4f8cff", "#ff8a3d", "#2fd47f", "#d36bff", "#f5d04a", "#ff5577"];
const ROLE_PATTERNS = {
  idle: /idle|stand|wait|breath/i,
  walk: /walk|run|move/i,
  attack: /attack|atk|slash|bite|hit|punch|cast|strike/i,
};
const LOOPING = /idle|walk|run|move|stand|loop/i;
const COMPASS_LABEL = { N: "N ↑", NE: "NE ↗", E: "E →", SE: "SE ↘", S: "S ↓", SW: "SW ↙", W: "W ←", NW: "NW ↖" };

export function guessRoles(types) {
  const pick = (re, fallback) => types.find((t) => re.test(t)) || fallback;
  return {
    idle: pick(ROLE_PATTERNS.idle, types[0]),
    walk: pick(ROLE_PATTERNS.walk, types[1] || types[0]),
    attack: pick(ROLE_PATTERNS.attack, types[2] || types[0]),
  };
}

let nextId = 1;

export class Actor {
  constructor(set, saved = {}) {
    this.id = nextId++;
    this.set = set;
    this.name = set.name;
    this.source = set.source;
    this.color = PALETTE[(this.id - 1) % PALETTE.length];
    this.x = 0;
    this.y = 0;
    this.dir = "S";
    this.state = "idle";
    this.t = 0;
    this.hitDone = false;
    this.target = null;
    this.input = null; // {x, y} direction from keyboard while controlled
    const guessed = guessRoles(set.action_types);
    this.bounds = setBounds(set, [saved.roles?.idle || guessed.idle, saved.roles?.walk || guessed.walk]);

    const meta = set.meta || {};
    const roles = { ...guessRoles(set.action_types), ...(saved.roles || {}) };
    const natural = meta.root_motion?.[roles.walk]?.speed_m_s ?? null;
    this.cfg = {
      roles,
      ppm: saved.ppm ?? meta.pixels_per_meter ?? 50, // sprites without a scale: ~50 px/m is a guess, tune it
      naturalSpeed: saved.natural_speed ?? natural,
      moveSpeed: saved.move_speed ?? natural ?? 1.5,
      syncAnim: saved.sync_anim ?? true,
      hitFrame: saved.hit_frame ?? null,
      attackRange: saved.attack_range ?? 1.2,
    };
  }

  anim(role, dir = this.dir) {
    return this.set.getAction(this.cfg.roles[role], dir);
  }

  isLoop(role) {
    const type = this.cfg.roles[role];
    const loop = this.set.meta?.loop?.[type];
    return loop ?? LOOPING.test(type);
  }

  /** Hit frame of the attack: manual override > "atk" event > the usual 59 %. */
  hitFrame() {
    const a = this.anim("attack", "S");
    if (!a) return 0;
    if (this.cfg.hitFrame != null) return Math.min(this.cfg.hitFrame, a.frames.length - 1);
    const ev = eventFrame(a, "atk");
    return ev >= 0 ? ev : Math.round((a.frames.length - 1) * DEFAULT_HIT_FRACTION);
  }

  hitSource() {
    if (this.cfg.hitFrame != null) return "manual";
    return eventFrame(this.anim("attack", "S"), "atk") >= 0 ? "atk event" : "default 59%";
  }

  /** Walk animation speed multiplier so the feet match the ground speed. */
  walkRate() {
    const n = this.cfg.naturalSpeed;
    return this.cfg.syncAnim && n ? this.cfg.moveSpeed / n : 1;
  }

  /** How much faster the body moves than the feet "push" (0 = no foot sliding). */
  slide() {
    const n = this.cfg.naturalSpeed;
    if (!n) return null;
    return (this.cfg.moveSpeed / (n * this.walkRate())) - 1;
  }

  play(state) {
    if (state === this.state) return;
    this.state = state;
    this.t = 0;
    this.hitDone = false;
  }

  frame() {
    const a = this.anim(this.state);
    if (!a) return null;
    return a.frames[frameIndexAt(a, this.t, this.isLoop(this.state))];
  }

  frameIndex() {
    const a = this.anim(this.state);
    return a ? frameIndexAt(a, this.t, this.isLoop(this.state)) : 0;
  }

  heightPx() {
    return Math.round(-this.bounds.y0);
  }

  /** Approximate real height: screen pixels -> metres, undoing the camera's vertical foreshortening. */
  heightM(elevationDeg) {
    return this.heightPx() / this.cfg.ppm / Math.cos((elevationDeg * Math.PI) / 180);
  }
}

export class MotionLab {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.actors = [];
    this.selected = null;
    this.dummies = [{ x: 2.2, y: 0, flash: 0, hits: 0 }, { x: -1.5, y: 1.8, flash: 0, hits: 0 }];
    this.effects = [];
    this.mode = "playground";
    this.lineup = { role: "walk", dir: "SE" };
    this.opts = { grid: true, ranges: true, treadmill: true, labels: true, follow: true };
    this.tileM = 1;
    this.elevation = 30;
    this.speedMul = 1;
    this.zoom = 1.6;
    this.cam = { x: 0, y: 0 };
    this.keys = new Set();
    this.time = 0;
    this.onHitListeners = [];
    this._drag = null;
    this._last = performance.now();
    this.active = false;
    this._bind();
    requestAnimationFrame((t) => this._tick(t));
  }

  get sinEl() {
    return Math.sin((this.elevation * Math.PI) / 180);
  }

  add(actor) {
    // Spawn on a free tile near the origin.
    const n = this.actors.length;
    actor.x = ((n % 4) - 1.5) * 2.4;
    actor.y = -Math.floor(n / 4) * 2.2;
    this.actors.push(actor);
    if (actor.source === "render" && actor.set.meta?.elevation_deg) this.elevation = actor.set.meta.elevation_deg;
    this.select(actor);
  }

  remove(actor) {
    this.actors = this.actors.filter((a) => a !== actor);
    if (this.selected === actor) this.select(this.actors[0] || null);
  }

  select(actor) {
    this.selected = actor;
    this.canvas.dispatchEvent(new CustomEvent("select", { detail: actor }));
  }

  restartAll() {
    for (const a of this.actors) { a.t = 0; a.hitDone = false; }
    this.time = 0;
  }

  // ───────── coordinates ─────────
  toScreen(x, y) {
    const k = P * this.zoom;
    return [this.w / 2 + (x - this.cam.x) * k, this.h / 2 + (y - this.cam.y) * k * this.sinEl];
  }

  toGround(sx, sy) {
    const k = P * this.zoom;
    return [this.cam.x + (sx - this.w / 2) / k, this.cam.y + (sy - this.h / 2) / (k * this.sinEl)];
  }

  /** Where an actor stands (lineup overrides positions). */
  posOf(actor) {
    if (this.mode !== "lineup") return [actor.x, actor.y];
    const i = this.actors.indexOf(actor);
    const n = this.actors.length;
    return [(i - (n - 1) / 2) * this._lineupSpacing(), 0];
  }

  _lineupSpacing() {
    let w = 1;
    for (const a of this.actors) w = Math.max(w, (a.bounds.x1 - a.bounds.x0) / a.cfg.ppm);
    return w + 0.6;
  }

  // ───────── actions ─────────
  attack(actor = this.selected) {
    if (!actor || actor.state === "attack" || !actor.anim("attack")) return;
    actor.target = null;
    // Auto-face the nearest dummy in reach, like clicking a monster in a 2D online game.
    let best = null;
    for (const d of this.dummies) {
      const dist = Math.hypot(d.x - actor.x, d.y - actor.y);
      if (dist < actor.cfg.attackRange * 2.5 && (!best || dist < best.dist)) best = { d, dist };
    }
    if (best) actor.dir = directionFromVector(best.d.x - actor.x, best.d.y - actor.y);
    actor.play("attack");
  }

  _resolveHit(actor) {
    const a = actor.anim("attack");
    const hitMs = Math.round(actor.hitFrame() * (a?.delay_ms || 0));
    const [ax, ay] = this.posOf(actor);
    const [fx, fy] = DIR_VECTORS[actor.dir];
    let landed = 0;
    if (this.mode === "playground") {
      for (const d of this.dummies) {
        const dx = d.x - ax, dy = d.y - ay;
        const dist = Math.hypot(dx, dy);
        const facing = dist < 1e-3 ? 1 : (dx * fx + dy * fy) / dist;
        if (dist <= actor.cfg.attackRange + 0.35 && facing > 0.38) { // within ~67° of facing
          d.flash = 260;
          d.hits++;
          landed++;
          this.effects.push({ kind: "text", x: d.x, y: d.y, text: `HIT ${hitMs}ms`, color: actor.color, life: 900 });
        }
      }
    }
    this.effects.push({ kind: "ring", x: ax, y: ay, r: actor.cfg.attackRange, color: actor.color, life: 350 });
    for (const fn of this.onHitListeners) fn(actor, hitMs, landed);
  }

  // ───────── update ─────────
  _update(dtMs) {
    const dt = (dtMs / 1000) * this.speedMul;
    this.time += dt;
    const lineup = this.mode === "lineup";

    if (this.mode === "compass") {
      const a = this.selected;
      if (a) {
        const role = this.lineup.role;
        if (a.state !== role) a.play(role);
        a.t += dt * 1000 * (role === "walk" ? a.walkRate() : 1);
        const anim = a.anim(role, "S");
        if (role === "attack" && anim && a.t >= actionDuration(anim)) a.t = 0;  // replay, no hit test
      }
      this.cam = { x: 0, y: 0 };
      return;
    }

    for (const a of this.actors) {
      if (lineup) {
        a.dir = this.lineup.dir;
        const role = this.lineup.role;
        if (a.state !== role) a.play(role);
        const rate = role === "walk" ? a.walkRate() : 1;
        a.t += dt * 1000 * rate;
        if (role === "attack") this._attackTick(a, true);
        continue;
      }

      if (a.state === "attack") {
        a.t += dt * 1000;
        this._attackTick(a, false);
        continue;
      }

      // Movement: keyboard input for the controlled actor, else a click target (8-way path: diagonal first, then straight).
      let vx = 0, vy = 0;
      if (a === this.selected && a.input && (a.input.x || a.input.y)) {
        a.target = null;
        vx = a.input.x; vy = a.input.y;
      } else if (a.target) {
        const dx = a.target.x - a.x, dy = a.target.y - a.y;
        const eps = 0.02;
        if (Math.abs(dx) < eps && Math.abs(dy) < eps) {
          a.target = null;
        } else {
          // Diagonal first, then straight - so the sprite always faces one of its 8 directions.
          vx = Math.abs(dx) > eps ? Math.sign(dx) : 0;
          vy = Math.abs(dy) > eps ? Math.sign(dy) : 0;
        }
      }
      if (vx || vy) {
        const len = Math.hypot(vx, vy);
        vx /= len; vy /= len;
        const step = a.cfg.moveSpeed * dt;
        let nx = a.x + vx * step, ny = a.y + vy * step;
        if (a.target) {
          if (Math.sign(a.target.x - nx) !== Math.sign(a.target.x - a.x)) nx = a.target.x;
          if (Math.sign(a.target.y - ny) !== Math.sign(a.target.y - a.y)) ny = a.target.y;
        }
        a.x = nx; a.y = ny;
        a.dir = directionFromVector(vx, vy);
        a.play("walk");
        a.t += dt * 1000 * a.walkRate();
      } else {
        a.play("idle");
        a.t += dt * 1000;
      }
    }

    for (const d of this.dummies) d.flash = Math.max(0, d.flash - dtMs);
    this.effects = this.effects.filter((e) => (e.life -= dtMs) > 0);

    if (this.opts.follow && !lineup && this.selected) {
      const k = Math.min(1, dtMs / 200);
      this.cam.x += (this.selected.x - this.cam.x) * k;
      this.cam.y += (this.selected.y - this.cam.y) * k;
    }
  }

  _attackTick(a, looping) {
    const anim = a.anim("attack");
    if (!anim) return;
    const idx = Math.floor(a.t / anim.delay_ms);
    if (!a.hitDone && idx >= a.hitFrame()) {
      a.hitDone = true;
      this._resolveHit(a);
    }
    const dur = actionDuration(anim);
    if (a.t >= dur) {
      if (looping) {
        a.t = 0; a.hitDone = false; // lineup: replay to compare timing
      } else {
        a.play("idle");
      }
    }
  }

  // ───────── input ─────────
  _bind() {
    const c = this.canvas;
    const local = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

    c.addEventListener("pointerdown", (e) => {
      if (!this.active) return;
      const [sx, sy] = local(e);
      c.setPointerCapture(e.pointerId);
      if (e.button === 1 || e.button === 2) {
        this._drag = { kind: "pan", sx, sy, cx: this.cam.x, cy: this.cam.y };
        this.opts.follow = false;
        return;
      }
      const hitActor = this._actorAt(sx, sy);
      if (hitActor) { this.select(hitActor); return; }
      if (this.mode !== "playground") return;
      const dummy = this._dummyAt(sx, sy);
      if (dummy) { this._drag = { kind: "dummy", dummy }; return; }
      const [gx, gy] = this.toGround(sx, sy);
      if (this.selected && this.selected.state !== "attack") {
        this.selected.target = { x: gx, y: gy };
        this.effects.push({ kind: "click", x: gx, y: gy, life: 400 });
      }
    });
    c.addEventListener("pointermove", (e) => {
      const d = this._drag;
      if (!d) return;
      const [sx, sy] = local(e);
      if (d.kind === "pan") {
        const k = P * this.zoom;
        this.cam.x = d.cx - (sx - d.sx) / k;
        this.cam.y = d.cy - (sy - d.sy) / (k * this.sinEl);
      } else if (d.kind === "dummy") {
        [d.dummy.x, d.dummy.y] = this.toGround(sx, sy);
      }
    });
    const end = () => { this._drag = null; };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoom = Math.max(0.3, Math.min(6, this.zoom * Math.exp(-e.deltaY * 0.0015)));
    }, { passive: false });
  }

  setKey(code, down) {
    if (down) this.keys.add(code); else this.keys.delete(code);
    const k = this.keys;
    const x = (k.has("KeyD") || k.has("ArrowRight") ? 1 : 0) - (k.has("KeyA") || k.has("ArrowLeft") ? 1 : 0);
    const y = (k.has("KeyS") || k.has("ArrowDown") ? 1 : 0) - (k.has("KeyW") || k.has("ArrowUp") ? 1 : 0);
    if (this.selected) this.selected.input = { x, y };
  }

  _actorAt(sx, sy) {
    const k = P * this.zoom;
    let hit = null;
    for (const a of this.actors) {
      const [x, y] = this.posOf(a);
      const [px, py] = this.toScreen(x, y);
      const s = k / a.cfg.ppm;
      const b = a.bounds;
      if (sx >= px + b.x0 * s && sx <= px + b.x1 * s && sy >= py + b.y0 * s && sy <= py + Math.max(b.y1, 4) * s) {
        if (!hit || y > hit.y) hit = { a, y };
      }
    }
    return hit?.a || null;
  }

  _dummyAt(sx, sy) {
    const k = P * this.zoom;
    return this.dummies.find((d) => {
      const [px, py] = this.toScreen(d.x, d.y);
      return Math.abs(sx - px) < 0.3 * k && sy < py + 0.2 * k && sy > py - 1.5 * k * Math.cos(this.elevation * Math.PI / 180);
    }) || null;
  }

  // ───────── render ─────────
  _tick(now) {
    requestAnimationFrame((t) => this._tick(t));
    const dt = Math.min(100, now - this._last);
    this._last = now;
    if (!this.active) return;
    this._resize();
    this._update(dt);
    this._render();
  }

  _resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (r.width !== this.w || r.height !== this.h || dpr !== this.dpr) {
      this.w = r.width; this.h = r.height; this.dpr = dpr;
      this.canvas.width = Math.round(r.width * dpr);
      this.canvas.height = Math.round(r.height * dpr);
    }
  }

  _render() {
    const { ctx, dpr } = this;
    const k = P * this.zoom;
    const sin = this.sinEl;
    const tileW = this.tileM * Math.SQRT2 * k;
    const tileH = tileW * sin;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const lineup = this.mode === "lineup";
    if (this.opts.grid) {
      const [ox, oy] = this.toScreen(0, 0);
      drawIsoGrid(ctx, { ox, oy, tileW, tileH, lineWidth: 1, bounds: { x0: 0, y0: 0, x1: this.w, y1: this.h }, color: "rgba(120,140,180,0.16)" });
    }

    // Treadmill patches: ground scrolls under each walking actor at its game move speed.
    if (lineup && this.opts.treadmill && this.lineup.role === "walk") {
      for (const a of this.actors) {
        const [x, y] = this.posOf(a);
        this._treadmill(a, x, y, this.lineup.dir, 1.3, k, tileW, tileH);
      }
    }

    if (this.mode === "compass") {
      this._renderCompass(k, tileW, tileH);
      return;
    }

    // Click marker / attack rings (ground layer)
    for (const e of this.effects) {
      const [px, py] = this.toScreen(e.x, e.y);
      if (e.kind === "click") {
        ctx.strokeStyle = `rgba(255,255,255,${e.life / 400})`;
        ctx.lineWidth = 1.5;
        tilePath(ctx, px, py, tileW * 0.5, tileH * 0.5);
        ctx.stroke();
      } else if (e.kind === "ring") {
        ctx.strokeStyle = e.color;
        ctx.globalAlpha = e.life / 350;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.ellipse(px, py, e.r * k, e.r * k * sin, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    // Y-sorted drawables: deeper (smaller y) first - this IS the pivot's job.
    const items = this.actors.map((a) => {
      const [x, y] = this.posOf(a);
      return { y, draw: () => this._drawActor(a, x, y, k) };
    });
    if (!lineup) for (const d of this.dummies) items.push({ y: d.y, draw: () => this._drawDummy(d, k) });
    items.sort((p, q) => p.y - q.y);
    for (const it of items) it.draw();

    for (const e of this.effects) {
      if (e.kind !== "text") continue;
      const [px, py] = this.toScreen(e.x, e.y);
      const rise = (1 - e.life / 900) * 40;
      ctx.font = "bold 13px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillStyle = "#000";
      ctx.fillText(e.text, px + 1, py - 1.6 * k * 0.866 - rise + 1);
      ctx.fillStyle = e.color;
      ctx.fillText(e.text, px, py - 1.6 * k * 0.866 - rise);
      ctx.textAlign = "start";
    }
  }

  /** Ground patch under (x, y) scrolling against `dir` at the actor's game speed (foot-slide check). */
  _treadmill(a, x, y, dir, r, k, tileW, tileH) {
    const { ctx } = this;
    const sin = this.sinEl;
    const [fx, fy] = DIR_VECTORS[dir];
    const [px, py] = this.toScreen(x, y);
    const travel = a.cfg.moveSpeed * this.time;
    const [ox, oy] = this.toScreen(x - fx * travel, y - fy * travel);
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(px, py, r * k, r * k * sin, 0, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,255,255,0.03)";
    ctx.fill();
    ctx.clip();
    drawIsoGrid(ctx, { ox, oy, tileW: tileW / 2, tileH: tileH / 2, lineWidth: 1.5, bounds: { x0: px - (r + 0.2) * k, y0: py - r * k, x1: px + (r + 0.2) * k, y1: py + r * k }, color: a.color + "88" });
    ctx.restore();
  }

  /** "8 Dirs": the selected actor once per direction, on a ring, each standing where it faces. */
  _renderCompass(k, tileW, tileH) {
    const { ctx } = this;
    const a = this.selected;
    if (!a) return;
    const sin = this.sinEl;
    const role = this.lineup.role;
    const type = a.cfg.roles[role];
    const r = Math.max(2.8, ((a.bounds.x1 - a.bounds.x0) / a.cfg.ppm) * 1.8);
    const items = Object.keys(DIR_VECTORS).map((dir) => {
      const [vx, vy] = DIR_VECTORS[dir];
      return { dir, x: vx * r, y: vy * r };
    });
    if (this.opts.treadmill && role === "walk") {
      for (const it of items) this._treadmill(a, it.x, it.y, it.dir, 0.75, k, tileW, tileH);
    }
    const s = k / a.cfg.ppm;
    items.sort((p, q) => p.y - q.y);
    for (const it of items) {
      const [px, py] = this.toScreen(it.x, it.y);
      ctx.fillStyle = "rgba(0,0,0,0.35)";
      ctx.beginPath();
      ctx.ellipse(px, py, 0.32 * k, 0.32 * k * sin, 0, 0, Math.PI * 2);
      ctx.fill();
      const anim = a.set.getAction(type, it.dir);
      if (!anim) continue;
      const idx = frameIndexAt(anim, a.t, a.isLoop(role));
      drawFrame(ctx, a.set, anim.frames[idx], px, py, { scale: s, smoothing: a.source === "render" ? true : s < 1 });
      if (this.opts.labels) {
        ctx.font = "11px ui-monospace, monospace";
        ctx.textAlign = "center";
        ctx.fillStyle = "rgba(230,233,239,0.8)";
        ctx.fillText(`${COMPASS_LABEL[it.dir]} · f${idx}/${anim.frames.length - 1}`, px, py + 0.32 * k * sin + 14);
        ctx.textAlign = "start";
      }
    }
  }

  _drawActor(a, x, y, k) {
    const { ctx } = this;
    const [px, py] = this.toScreen(x, y);
    const s = k / a.cfg.ppm;
    const sin = this.sinEl;

    // shadow
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.beginPath();
    ctx.ellipse(px, py, 0.32 * k, 0.32 * k * sin, 0, 0, Math.PI * 2);
    ctx.fill();

    if (this.opts.ranges && (a === this.selected || this.mode === "lineup")) {
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = a.color + "aa";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(px, py, a.cfg.attackRange * k, a.cfg.attackRange * k * sin, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const frame = a.frame();
    drawFrame(ctx, a.set, frame, px, py, { scale: s, smoothing: a.source === "render" ? true : s < 1 });

    if (a === this.selected && this.mode === "playground") {
      ctx.strokeStyle = a.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(px, py, 0.38 * k, 0.38 * k * sin, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (this.opts.labels) {
      const top = py + a.bounds.y0 * s - 6;
      ctx.font = "11px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillStyle = a.color;
      ctx.fillText(a.name, px, top);
      if (this.mode === "lineup") {
        const anim = a.anim(a.state);
        const idx = a.frameIndex();
        ctx.fillStyle = "rgba(230,233,239,0.75)";
        ctx.fillText(`${a.state} f${idx}/${(anim?.frames.length || 1) - 1}`, px, py + 0.5 * k * sin + 14);
        if (a.state === "attack" && idx >= a.hitFrame() && idx <= a.hitFrame() + 1) {
          ctx.fillStyle = "#ff3b4f";
          ctx.fillText("● HIT", px, py + 0.5 * k * sin + 28);
        }
      }
      ctx.textAlign = "start";
    }
  }

  _drawDummy(d, k) {
    const { ctx } = this;
    const [px, py] = this.toScreen(d.x, d.y);
    const sin = this.sinEl;
    const cos = Math.cos((this.elevation * Math.PI) / 180);
    const h = 1.4 * k * cos;
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.beginPath();
    ctx.ellipse(px, py, 0.3 * k, 0.3 * k * sin, 0, 0, Math.PI * 2);
    ctx.fill();
    const flash = d.flash > 0;
    ctx.fillStyle = flash ? "#ffdddd" : "#8a6a48";
    ctx.strokeStyle = "#2a1d12";
    ctx.lineWidth = 1.5;
    ctx.fillRect(px - 0.06 * k, py - h, 0.12 * k, h);           // post
    ctx.strokeRect(px - 0.06 * k, py - h, 0.12 * k, h);
    ctx.fillStyle = flash ? "#ff5566" : "#c9a36b";
    ctx.beginPath();                                              // straw body
    ctx.ellipse(px, py - h * 0.7, 0.22 * k, 0.32 * k * cos, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "rgba(230,233,239,0.6)";
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.fillText(`dummy · ${d.hits} hits`, px, py + 0.3 * k * sin + 12);
    ctx.textAlign = "start";
  }
}
