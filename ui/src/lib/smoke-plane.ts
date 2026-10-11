/** The empty state's toy: a little "smoke plane" that wanders slow, gentle loops around the screen, leaving a soft
 *  white smoke trail that the air carries off. Every few minutes it dips away into the background and crashes
 *  softly (a puff of smoke and a few small flames); a new one flies in 3-6 s later.
 *
 *  Air: the same 2-D fluid as before (fluid2d.ts: stable fluids with vorticity confinement), in screen pixels. The
 *  plane stirs it (a push along its path and alternating wake swirls), random breezes cross it every 6-15 s.
 *  Smoke: inertia-free grains emitted at the plane, carried by the air, rising a little, spreading and fading.
 *  Rendering: a coarse density grid, Beer-Lambert opacity (kept low: calm, not distracting), soft lighting from
 *  the upper left and a faint drop shadow; flames are a handful of additive sprites. Capped at 30 fps; nothing
 *  runs when the canvas is gone (the caller stops it) or the window is hidden (no animation frames). */
import { Fluid2D } from "./fluid2d"

type Breeze = { t0: number; dx: number; dy: number; s: number; front0: number; speed: number; len: number }
type Flame = { x: number; y: number; vx: number; vy: number; age: number; life: number; size: number }

const TAU = Math.PI * 2
const rnd = (a: number, b: number) => a + Math.random() * (b - a)

export class SmokePlane {
  canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private W = 0; private H = 0                       // css px
  private fluid!: Fluid2D
  // grains
  private cap: number
  private n = 0
  private gx: Float32Array; private gy: Float32Array; private gvx: Float32Array; private gvy: Float32Array
  private gage: Float32Array; private glife: Float32Array; private gw: Float32Array
  // the plane
  private px = 0; private py = 0; private heading = 0; private speed = 48   // px/s: slow and gentle
  private turn = 0                                    // rad/s
  private loopUntil = -1; private loopDir = 1; private nextLoop = 0
  private depth = 0                                   // 0 = near, 1 = gone into the background (the dive)
  private phase: "fly" | "dive" | "gone" = "gone"
  private phaseAt = 0
  private nextCrash = 0
  private wakeSign = 1; private nextWake = 0
  private flames: Flame[] = []
  // air
  private breezes: Breeze[] = []
  private nextBreeze = 0
  private t = 0
  private raf = 0
  private last = 0
  /** frame cost (ms, smoothed) for the perf checks */
  frameMs = 0

  constructor(canvas: HTMLCanvasElement, cap = 4200) {
    this.canvas = canvas
    this.ctx = canvas.getContext("2d")!
    this.cap = cap
    const F = () => new Float32Array(cap)
    this.gx = F(); this.gy = F(); this.gvx = F(); this.gvy = F(); this.gage = F(); this.glife = F(); this.gw = F()
  }

  /** Size of the area in css px (call on mount and on resize). */
  resize(w: number, h: number) {
    if (Math.abs(w - this.W) < 2 && Math.abs(h - this.H) < 2 && this.fluid) return
    this.W = Math.max(200, w); this.H = Math.max(160, h)
    const cell = Math.max(this.W / 96, this.H / 60)
    this.fluid = new Fluid2D(Math.ceil(this.W / cell), Math.ceil(this.H / cell), cell, 0, 0)
    this.canvas.width = Math.round(this.W); this.canvas.height = Math.round(this.H)   // 1x: smoke is soft anyway
    this.gridW = 0                                      // rebuild the density grid
    if (this.phase === "gone" && this.t === 0) this.enter(true)
  }

  start() {
    this.last = performance.now()
    const loop = (now: number) => {
      this.raf = requestAnimationFrame(loop)
      if (now - this.last < 1000 / 31) return           // ~30 fps is plenty for drifting smoke
      const dt = Math.min(0.05, (now - this.last) / 1000)
      this.last = now
      const t0 = performance.now()
      this.step(dt)
      this.draw()
      this.frameMs = this.frameMs * 0.9 + (performance.now() - t0) * 0.1
    }
    this.raf = requestAnimationFrame(loop)
  }

  stop() { cancelAnimationFrame(this.raf); this.raf = 0 }

  /** Crash now (for testing; normally every 2-5 minutes). */
  crashSoon() { if (this.phase === "fly") this.nextCrash = this.t }

  get state() { return { phase: this.phase, grains: this.n, x: Math.round(this.px), y: Math.round(this.py), depth: +this.depth.toFixed(2) } }

  // ------------------------------------------------------------------ the plane

  /** A new plane comes in from just off one edge, heading into the screen. */
  private enter(first = false) {
    const W = this.W, H = this.H, side = (Math.random() * 4) | 0
    if (first) { this.px = W * rnd(0.3, 0.7); this.py = H * rnd(0.3, 0.6); this.heading = rnd(0, TAU) }
    else {
      const m = 30
      ;[this.px, this.py] = side === 0 ? [-m, rnd(0.2, 0.8) * H] : side === 1 ? [W + m, rnd(0.2, 0.8) * H]
        : side === 2 ? [rnd(0.2, 0.8) * W, -m] : [rnd(0.2, 0.8) * W, H + m]
      this.heading = Math.atan2(H / 2 - this.py, W / 2 - this.px) + rnd(-0.4, 0.4)
    }
    this.phase = "fly"; this.phaseAt = this.t; this.depth = 0; this.turn = 0
    this.nextCrash = this.t + rnd(120, 300)
    this.nextLoop = this.t + rnd(12, 30)
  }

  private steer(dt: number) {
    const t = this.t, W = this.W, H = this.H
    // wander: a slowly changing turn, sometimes a full loop
    let want = 0.42 * Math.sin(t * 0.21 + 1.3) + 0.28 * Math.sin(t * 0.47 + 4.1) + 0.12 * Math.sin(t * 1.1)
    if (t >= this.nextLoop && this.loopUntil < 0 && this.phase === "fly") {
      this.loopDir = Math.random() < 0.5 ? -1 : 1
      this.loopUntil = t + TAU / 1.05                      // one full turn at ~1 rad/s
      this.nextLoop = t + rnd(18, 40)
    }
    if (this.loopUntil > 0) { want = this.loopDir * 1.05; if (t > this.loopUntil) this.loopUntil = -1 }
    // keep inside the screen: near an edge, turn towards the middle (gently, more the closer it gets)
    const m = Math.min(W, H) * 0.16
    const edge = Math.max(0, m - this.px, this.px - (W - m), m - this.py, this.py - (H - m)) / m
    if (edge > 0 && this.phase === "fly" && this.t - this.phaseAt > 2.5) {
      const toMid = Math.atan2(H / 2 - this.py, W / 2 - this.px)
      let d = toMid - this.heading
      d = Math.atan2(Math.sin(d), Math.cos(d))
      want = want * (1 - Math.min(1, edge)) + Math.sign(d) * Math.min(1.3, 0.5 + edge * 1.2)
      this.loopUntil = -1
    }
    if (this.phase === "dive") want = 0.35 * this.loopDir         // a lazy spiral down into the background
    this.turn += (want - this.turn) * Math.min(1, dt * 1.6)       // eased: no sudden banking
    this.heading += this.turn * dt
    const sp = this.phase === "dive" ? this.speed * (1 - 0.6 * this.depth) : this.speed * (1 - 0.18 * Math.abs(this.turn))
    // the air nudges the plane a little too
    const air = [0, 0]
    this.fluid.sample(this.px, this.py, air)
    this.px += (Math.cos(this.heading) * sp + air[0] * 0.25) * dt
    this.py += (Math.sin(this.heading) * sp + air[1] * 0.25) * dt + (this.phase === "dive" ? 14 * dt : 0)
  }

  private emit(dt: number) {
    if (this.phase === "gone") return
    const near = 1 - this.depth
    const count = Math.round(rnd(9, 13) * near * (dt * 30))
    const bx = -Math.cos(this.heading), by = -Math.sin(this.heading)
    for (let k = 0; k < count; k++) {
      const i = this.spawnSlot()
      if (i < 0) return
      const back = rnd(0, 4)
      this.gx[i] = this.px + bx * back + rnd(-1.5, 1.5); this.gy[i] = this.py + by * back + rnd(-1.5, 1.5)
      this.gvx[i] = bx * rnd(4, 14); this.gvy[i] = by * rnd(4, 14)
      this.gage[i] = 0; this.glife[i] = rnd(5, 9) * (0.6 + 0.4 * near)
      this.gw[i] = rnd(0.7, 1.3) * (0.35 + 0.65 * near)
    }
    // the plane stirs the air: a push along its path and alternating wake swirls (a small vortex street)
    const f = this.fluid
    f.push(this.px, this.py, 22, Math.cos(this.heading) * 30 * dt * near, Math.sin(this.heading) * 30 * dt * near)
    if (this.t >= this.nextWake) {
      this.nextWake = this.t + 0.8
      this.wakeSign = -this.wakeSign
      f.swirl(this.px + bx * 18, this.py + by * 18, 18, this.wakeSign * 2.4 * near)
    }
  }

  /** A free grain slot (oldest first when full). */
  private spawnSlot() {
    if (this.n < this.cap) return this.n++
    let best = 0, worst = -1
    for (let i = 0; i < this.n; i += 7) { const left = this.glife[i] - this.gage[i]; if (worst < 0 || left < worst) { worst = left; best = i } }
    return best
  }

  /** The soft crash: a puff of smoke, a few small flames, then nothing for 3-6 s. */
  private crash() {
    const f = this.fluid, x = this.px, y = this.py
    for (let k = 0; k < 950; k++) {
      const i = this.spawnSlot()
      if (i < 0) break
      const a = rnd(0, TAU), s = rnd(10, 70) * Math.sqrt(Math.random())
      this.gx[i] = x + rnd(-3, 3); this.gy[i] = y + rnd(-3, 3)
      this.gvx[i] = Math.cos(a) * s; this.gvy[i] = Math.sin(a) * s - rnd(4, 16)
      this.gage[i] = 0; this.glife[i] = rnd(3, 6.5); this.gw[i] = rnd(0.7, 1.2)
    }
    for (let r = 0; r < 6; r++) {
      const a = (r / 6) * TAU
      f.push(x + Math.cos(a) * 14, y + Math.sin(a) * 14, 20, Math.cos(a) * 18, Math.sin(a) * 18 - 4)
    }
    f.swirl(x - 12, y - 6, 22, 9); f.swirl(x + 12, y - 6, 22, -9)
    const nf = 10 + ((Math.random() * 7) | 0)
    for (let k = 0; k < nf; k++)
      this.flames.push({ x: x + rnd(-14, 14), y: y + rnd(-4, 8), vx: rnd(-8, 8), vy: rnd(-30, -10), age: -rnd(0, 0.5),
        life: rnd(0.9, 2.1), size: rnd(4, 8) })
    this.phase = "gone"; this.phaseAt = this.t
    this.reenterAt = this.t + rnd(3, 6)
  }
  private reenterAt = 0

  // ------------------------------------------------------------------ the air

  private air(dt: number) {
    const f = this.fluid, t = this.t
    if (t >= this.nextBreeze) {                         // a random breeze, now and then
      const fromLeft = Math.random() < 0.68
      const a = (fromLeft ? 0 : Math.PI) + rnd(-0.5, 0.5)
      let dx = Math.cos(a), dy = Math.sin(a) * 0.6 - 0.1
      const nrm = Math.hypot(dx, dy); dx /= nrm; dy /= nrm
      const ext = Math.hypot(this.W, this.H) / 2 + 40
      const speed = rnd(70, 140), dur = rnd(1.6, 3.6)
      this.breezes.push({ t0: t, dx, dy, s: rnd(18, 42), front0: -ext, speed, len: speed * dur })
      this.nextBreeze = t + rnd(6, 15)
    }
    const S = f.nx + 2, cx = this.W / 2, cy = this.H / 2
    for (const b of this.breezes) {
      const front = b.front0 + b.speed * (t - b.t0)
      for (let j = 1; j <= f.ny; j++) {
        const wy = f.y0 + (j - 0.5) * f.h - cy
        for (let i = 1; i <= f.nx; i++) {
          const wx = f.x0 + (i - 0.5) * f.h - cx
          const behind = front - (wx * b.dx + wy * b.dy)
          if (behind <= 0 || behind >= b.len) continue
          const across = wx * -b.dy + wy * b.dx
          const e = Math.min(1, behind / 30) * Math.min(1, (b.len - behind) / 40) *
            (0.7 + 0.3 * Math.sin(across * 0.05 + b.t0 * 7 + t * 2.3))
          const k = i + S * j, rate = Math.min(1, 2 * e * dt)
          f.u[k] += (b.dx * b.s * e - f.u[k]) * rate
          f.v[k] += (b.dy * b.s * e - f.v[k]) * rate
        }
      }
      if (Math.random() < 0.3) {
        const along = front - Math.random() * Math.min(b.len, 200), off = rnd(-0.5, 0.5) * this.H
        f.swirl(cx + along * b.dx - off * b.dy, cy + along * b.dy + off * b.dx, rnd(14, 30), (Math.random() < 0.5 ? -1 : 1) * b.s * rnd(0.15, 0.3))
      }
    }
    this.breezes = this.breezes.filter((b) => b.front0 + b.speed * (t - b.t0) - b.len < Math.hypot(this.W, this.H))
    if (Math.random() < 0.06) f.swirl(rnd(0, this.W), rnd(0, this.H), rnd(14, 28), (Math.random() < 0.5 ? -1 : 1) * rnd(2, 5))
    f.step(dt)
  }

  private step(dt: number) {
    if (!this.fluid) return
    this.t += dt
    const t = this.t
    this.air(dt)
    if (this.phase === "fly") {
      this.steer(dt)
      if (t >= this.nextCrash) { this.phase = "dive"; this.phaseAt = t; this.loopDir = Math.random() < 0.5 ? -1 : 1 }
    } else if (this.phase === "dive") {
      this.steer(dt)
      this.depth = Math.min(1, (t - this.phaseAt) / 3.4)    // dips away into the background over ~3.4 s
      if (this.depth >= 1) this.crash()
    } else if (t >= this.reenterAt && this.reenterAt > 0) {
      this.reenterAt = 0
      this.enter()
    }
    this.emit(dt)
    // grains: carried by the air, a little lift, spreading with age (turbulent diffusion)
    const f = this.fluid, sv = [0, 0]
    let live = 0
    for (let i = 0; i < this.n; i++) {
      const a = this.gage[i] + dt
      if (a >= this.glife[i]) continue                  // dead: compacted away below
      f.sample(this.gx[i], this.gy[i], sv)
      const spread = 5 + 70 * (a / this.glife[i])          // smoke widens as it ages
      const drag = Math.min(1, dt * 2.5)
      this.gvx[i] += (sv[0] - this.gvx[i]) * drag; this.gvy[i] += (sv[1] - 5 - this.gvy[i]) * drag
      this.gx[i] += (this.gvx[i] + rnd(-0.5, 0.5) * spread) * dt
      this.gy[i] += (this.gvy[i] + rnd(-0.5, 0.5) * spread) * dt
      this.gage[i] = a
      if (live !== i) {
        this.gx[live] = this.gx[i]; this.gy[live] = this.gy[i]; this.gvx[live] = this.gvx[i]; this.gvy[live] = this.gvy[i]
        this.gage[live] = this.gage[i]; this.glife[live] = this.glife[i]; this.gw[live] = this.gw[i]
      }
      live++
    }
    this.n = live
    for (const fl of this.flames) {
      fl.age += dt; fl.x += fl.vx * dt; fl.y += fl.vy * dt; fl.vx *= 0.96; fl.vy *= 0.97
    }
    this.flames = this.flames.filter((fl) => fl.age < fl.life)
  }

  // ------------------------------------------------------------------ rendering

  private cell = 3                                     // css px per density cell
  private gridW = 0; private gridH = 0
  private grid = new Float32Array(0); private tmp = new Float32Array(0); private sgrid = new Float32Array(0)
  private off: HTMLCanvasElement | null = null; private octx: CanvasRenderingContext2D | null = null
  private img: ImageData | null = null; private px32: Uint32Array | null = null
  private soff: HTMLCanvasElement | null = null; private sctx: CanvasRenderingContext2D | null = null
  private simg: ImageData | null = null; private spx32: Uint32Array | null = null
  private static OPA = (() => {                        // opacity of smoke of depth x: 1 − e^(−x), kept soft (≤ 0.62)
    const l = new Uint8Array(1024)
    for (let i = 0; i < 1024; i++) l[i] = Math.round(158 * (1 - Math.exp(-(i / 1023) * 5)))
    return l
  })()
  private static TRANS = (() => {
    const l = new Float32Array(1024)
    for (let i = 0; i < 1024; i++) l[i] = Math.exp(-(i / 1023) * 6)
    return l
  })()

  private blur(g: Float32Array, w: number, h: number) {
    const tmp = this.tmp
    for (let y = 0; y < h; y++) { const r = y * w; for (let x = 1; x < w - 1; x++) tmp[r + x] = (g[r + x - 1] + 2 * g[r + x] + g[r + x + 1]) * 0.25 }
    for (let y = 1; y < h - 1; y++) { const r = y * w; for (let x = 0; x < w; x++) g[r + x] = (tmp[r + x - w] + 2 * tmp[r + x] + tmp[r + x + w]) * 0.25 }
  }

  private draw() {
    const { ctx } = this
    const R = this.cell, gw = Math.ceil(this.W / R), gh = Math.ceil(this.H / R)
    if (gw !== this.gridW || gh !== this.gridH || !this.off) {
      this.gridW = gw; this.gridH = gh
      this.grid = new Float32Array(gw * gh); this.tmp = new Float32Array(gw * gh); this.sgrid = new Float32Array(gw * gh)
      this.off = document.createElement("canvas"); this.off.width = gw; this.off.height = gh
      this.octx = this.off.getContext("2d")!; this.img = this.octx.createImageData(gw, gh); this.px32 = new Uint32Array(this.img.data.buffer)
      this.soff = document.createElement("canvas"); this.soff.width = gw; this.soff.height = gh
      this.sctx = this.soff.getContext("2d")!; this.simg = this.sctx.createImageData(gw, gh); this.spx32 = new Uint32Array(this.simg.data.buffer)
    }
    const g = this.grid, sg = this.sgrid
    g.fill(0); sg.fill(0)
    const splat = (grid: Float32Array, fx: number, fy: number, w: number) => {
      const x0 = fx | 0, y0 = fy | 0
      if (x0 < 0 || y0 < 0 || x0 >= gw - 1 || y0 >= gh - 1) return
      const ax = fx - x0, ay = fy - y0, k = y0 * gw + x0
      grid[k] += w * (1 - ax) * (1 - ay); grid[k + 1] += w * ax * (1 - ay)
      grid[k + gw] += w * (1 - ax) * ay; grid[k + gw + 1] += w * ax * ay
    }
    const W0 = 0.05
    for (let i = 0; i < this.n; i++) {
      const u = this.gage[i] / this.glife[i]
      const a = Math.min(1, this.gage[i] / 0.15) * (1 - u) ** 0.9
      const w = W0 * a * this.gw[i]
      const fx = this.gx[i] / R, fy = this.gy[i] / R
      splat(g, fx, fy, w)
      splat(sg, fx + 6 / R, fy + 10 / R, w)               // shadow: away from the light at the upper left
    }
    // the plane itself: a slightly denser knot of smoke at the head
    if (this.phase !== "gone") {
      const near = 1 - this.depth
      for (let k = 0; k < 3; k++) splat(g, (this.px - Math.cos(this.heading) * k * 2) / R, (this.py - Math.sin(this.heading) * k * 2) / R, 0.35 * near)
    }
    this.blur(g, gw, gh); this.blur(sg, gw, gh); this.blur(sg, gw, gh)
    const OPA = SmokePlane.OPA, TR = SmokePlane.TRANS, px = this.px32!, spx = this.spx32!
    const q = (d: number) => (d >= 1 ? 1023 : (d * 1023) | 0)
    const s2 = 2 * (-1 - gw), s4 = 4 * (-1 - gw)
    for (let k = 0; k < g.length; k++) {
      const d = g[k]
      if (d <= 0.0006) { px[k] = 0 } else {
        const lit = TR[q(k + s4 >= 0 ? (g[k + s2] + g[k + s4]) * 0.3 : 0)]
        const br = 0.76 + 0.3 * lit
        const r = Math.min(255, 236 * br) | 0, gg = Math.min(255, 233 * br) | 0, b = Math.min(255, 226 * br + (1 - lit) * 5) | 0
        px[k] = ((OPA[q(d)] << 24) | (b << 16) | (gg << 8) | r) >>> 0
      }
      const sd = sg[k]
      spx[k] = sd <= 0.0006 ? 0 : (((OPA[q(sd)] * 0.28) | 0) << 24) >>> 0
    }
    this.octx!.putImageData(this.img!, 0, 0)
    this.sctx!.putImageData(this.simg!, 0, 0)
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
    ctx.imageSmoothingEnabled = true
    ctx.globalCompositeOperation = "source-over"
    ctx.drawImage(this.soff!, 0, 0, this.canvas.width, this.canvas.height)
    ctx.drawImage(this.off!, 0, 0, this.canvas.width, this.canvas.height)
    // flames: a few small flickering sprites, additive
    if (this.flames.length) {
      ctx.globalCompositeOperation = "lighter"
      for (const fl of this.flames) {
        if (fl.age < 0) continue
        const u = fl.age / fl.life, flick = 0.75 + 0.25 * Math.sin(fl.age * 40 + fl.x)
        const r = fl.size * (1 - u * 0.5) * flick
        const grd = ctx.createRadialGradient(fl.x, fl.y, 0, fl.x, fl.y, r * 2.2)
        const a = Math.min(1, fl.age / 0.12) * (1 - u) * 0.7
        grd.addColorStop(0, `rgba(255, 236, 190, ${a})`)
        grd.addColorStop(0.35, `rgba(255, 160, 70, ${a * 0.7})`)
        grd.addColorStop(1, "rgba(200, 60, 20, 0)")
        ctx.fillStyle = grd
        ctx.beginPath(); ctx.ellipse(fl.x, fl.y - r * 0.4, r * 1.3, r * 2.2, 0, 0, TAU); ctx.fill()
      }
      ctx.globalCompositeOperation = "source-over"
    }
  }
}
