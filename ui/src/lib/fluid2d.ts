/** A small 2-D incompressible fluid on a grid (Stam's "stable fluids"), for the smoke on the empty state.
 *
 *  Each step: vorticity confinement (puts back the small swirls the coarse grid smears out), semi-Lagrangian
 *  self-advection of the velocity, a pressure projection that makes the flow divergence-free (so air goes around
 *  and curls up instead of piling up), and a gentle decay so the air settles after a breeze. The borders are open:
 *  zero pressure, velocity copied outward, so breezes pass through. Units: the caller's world units and seconds;
 *  cell (0,0) is centred on (x0 + h/2, y0 + h/2). Forces are added straight into u, v before `step`. */
export class Fluid2D {
  readonly nx: number; readonly ny: number; readonly h: number; readonly x0: number; readonly y0: number
  readonly u: Float32Array; readonly v: Float32Array
  private u0: Float32Array; private v0: Float32Array; private p: Float32Array; private div: Float32Array
  private w: Float32Array
  /** vorticity confinement strength; damping per second (fraction of velocity kept after 1 s) */
  eps = 4
  keep = 0.55
  iters = 14

  constructor(nx: number, ny: number, h: number, x0: number, y0: number) {
    this.nx = nx; this.ny = ny; this.h = h; this.x0 = x0; this.y0 = y0
    const N = (nx + 2) * (ny + 2), F = () => new Float32Array(N)
    this.u = F(); this.v = F(); this.u0 = F(); this.v0 = F(); this.p = F(); this.div = F(); this.w = F()
  }

  IX(i: number, j: number) { return i + (this.nx + 2) * j }

  /** Grid coordinates (cells, centres at integers 1..n) of a world point. */
  gx(x: number) { return (x - this.x0) / this.h + 0.5 }
  gy(y: number) { return (y - this.y0) / this.h + 0.5 }

  /** Velocity at a world point (bilinear) -> out[0..1]; returns false outside the grid. */
  sample(x: number, y: number, out: Float32Array | number[]) {
    const fx = this.gx(x), fy = this.gy(y)
    if (fx < 1 || fy < 1 || fx >= this.nx || fy >= this.ny) { out[0] = 0; out[1] = 0; return false }
    const i = fx | 0, j = fy | 0, a = fx - i, b = fy - j, S = this.nx + 2, k = i + S * j
    const u = this.u, v = this.v
    out[0] = (u[k] * (1 - a) + u[k + 1] * a) * (1 - b) + (u[k + S] * (1 - a) + u[k + S + 1] * a) * b
    out[1] = (v[k] * (1 - a) + v[k + 1] * a) * (1 - b) + (v[k + S] * (1 - a) + v[k + S + 1] * a) * b
    return true
  }

  /** Add a velocity change to the cells within radius r (world units) of (x, y), falling off smoothly. */
  push(x: number, y: number, r: number, du: number, dv: number) {
    const cx = this.gx(x), cy = this.gy(y), rc = r / this.h, S = this.nx + 2
    const i0 = Math.max(1, Math.floor(cx - rc)), i1 = Math.min(this.nx, Math.ceil(cx + rc))
    const j0 = Math.max(1, Math.floor(cy - rc)), j1 = Math.min(this.ny, Math.ceil(cy + rc))
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const d2 = ((i - cx) ** 2 + (j - cy) ** 2) / (rc * rc)
      if (d2 >= 1) continue
      const f = (1 - d2) * (1 - d2)
      this.u[i + S * j] += du * f; this.v[i + S * j] += dv * f
    }
  }

  /** A small vortex (a swirl of strength `s`, world units/s at its rim) centred at (x, y). */
  swirl(x: number, y: number, r: number, s: number) {
    const cx = this.gx(x), cy = this.gy(y), rc = r / this.h, S = this.nx + 2
    const i0 = Math.max(1, Math.floor(cx - rc)), i1 = Math.min(this.nx, Math.ceil(cx + rc))
    const j0 = Math.max(1, Math.floor(cy - rc)), j1 = Math.min(this.ny, Math.ceil(cy + rc))
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const dx = (i - cx) / rc, dy = (j - cy) / rc, d2 = dx * dx + dy * dy
      if (d2 >= 1) continue
      const f = s * (1 - d2) * 2
      this.u[i + S * j] += -dy * f; this.v[i + S * j] += dx * f
    }
  }

  step(dt: number) {
    this.confine(dt)
    this.u0.set(this.u); this.v0.set(this.v)
    this.advect(this.u, this.u0, dt); this.advect(this.v, this.v0, dt)
    this.edges()
    this.project()
    const k = Math.pow(this.keep, dt)
    for (let n = 0; n < this.u.length; n++) { this.u[n] *= k; this.v[n] *= k }
  }

  /** Vorticity confinement: f = ε·h·(N × ω), N = ∇|ω| / |∇|ω||. */
  private confine(dt: number) {
    const { nx, ny, u, v, w, h } = this, S = nx + 2
    for (let j = 1; j <= ny; j++) for (let i = 1; i <= nx; i++) {
      const k = i + S * j
      w[k] = (v[k + 1] - v[k - 1] - u[k + S] + u[k - S]) / (2 * h)
    }
    const e = this.eps * h * dt
    for (let j = 2; j < ny; j++) for (let i = 2; i < nx; i++) {
      const k = i + S * j
      const gx = (Math.abs(w[k + 1]) - Math.abs(w[k - 1])) * 0.5, gy = (Math.abs(w[k + S]) - Math.abs(w[k - S])) * 0.5
      const len = Math.sqrt(gx * gx + gy * gy) + 1e-5
      u[k] += e * (gy / len) * w[k]
      v[k] -= e * (gx / len) * w[k]
    }
  }

  private advect(d: Float32Array, d0: Float32Array, dt: number) {
    const { nx, ny, u0, v0 } = this, S = nx + 2, s = dt / this.h
    for (let j = 1; j <= ny; j++) for (let i = 1; i <= nx; i++) {
      const k = i + S * j
      let x = i - s * u0[k], y = j - s * v0[k]
      x = x < 0.5 ? 0.5 : x > nx + 0.5 ? nx + 0.5 : x
      y = y < 0.5 ? 0.5 : y > ny + 0.5 ? ny + 0.5 : y
      const i0 = x | 0, j0 = y | 0, a = x - i0, b = y - j0, q = i0 + S * j0
      d[k] = (d0[q] * (1 - a) + d0[q + 1] * a) * (1 - b) + (d0[q + S] * (1 - a) + d0[q + S + 1] * a) * b
    }
  }

  /** Open borders: velocity copied outward from the first inner cell. */
  private edges() {
    const { nx, ny, u, v } = this, S = nx + 2
    for (let i = 1; i <= nx; i++) {
      u[i] = u[i + S]; v[i] = v[i + S]
      u[i + S * (ny + 1)] = u[i + S * ny]; v[i + S * (ny + 1)] = v[i + S * ny]
    }
    for (let j = 0; j <= ny + 1; j++) {
      u[S * j] = u[1 + S * j]; v[S * j] = v[1 + S * j]
      u[nx + 1 + S * j] = u[nx + S * j]; v[nx + 1 + S * j] = v[nx + S * j]
    }
  }

  /** Pressure projection (Gauss–Seidel, zero pressure at the open borders). */
  private project() {
    const { nx, ny, u, v, p, div, h } = this, S = nx + 2
    for (let j = 1; j <= ny; j++) for (let i = 1; i <= nx; i++) {
      const k = i + S * j
      div[k] = -0.5 * h * (u[k + 1] - u[k - 1] + v[k + S] - v[k - S])
      p[k] = 0
    }
    for (let it = 0; it < this.iters; it++)
      for (let j = 1; j <= ny; j++) for (let i = 1; i <= nx; i++) {
        const k = i + S * j
        p[k] = (div[k] + p[k - 1] + p[k + 1] + p[k - S] + p[k + S]) * 0.25
      }
    for (let j = 1; j <= ny; j++) for (let i = 1; i <= nx; i++) {
      const k = i + S * j
      u[k] -= (0.5 * (p[k + 1] - p[k - 1])) / h
      v[k] -= (0.5 * (p[k + S] - p[k - S])) / h
    }
    this.edges()
  }
}
