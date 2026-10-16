"""Generate the Zen ensō as SVG paths.

The ensō is one brush stroke: the outline of a variable-width stroke around a slightly wobbly circle, with a heavy
loaded start, a tapered end, an open gap at the top right, and (optionally) a dry-brush tail where the stroke splits
into bristle streaks. Everything is deterministic (seeded), so the output is stable and can be pasted into
Logo.tsx / favicon.svg.

    python scripts/enso.py variants   -> scripts/out/enso-variants.html (preview sheet)
    python scripts/enso.py <name>     -> prints the path data for one variant
"""
import json
import math
import random
import sys
from pathlib import Path

VARIANTS = {
    # name: centre radius, base width, start angle (deg, clockwise from 3 o'clock, y down), sweep, wobble,
    #       dry tail (number of bristle streaks, 0 = none), seed
    "classic": dict(r=11.2, w=3.4, start=-28, sweep=318, wobble=0.035, bristles=5, seed=7),
    "open":    dict(r=11.6, w=2.4, start=-18, sweep=296, wobble=0.03, bristles=4, seed=3),
    "bold":    dict(r=10.6, w=4.6, start=-34, sweep=330, wobble=0.04, bristles=6, seed=11),
    # favicon: bolder, simpler, no bristles, so it reads at 16 px
    "favicon": dict(r=11.4, w=6.0, start=-30, sweep=312, wobble=0.02, bristles=0, seed=7),
}

CX = CY = 16.0
N = 160  # samples along the stroke (the export uses fewer)
PREC = 2  # decimals in path data


def _smooth_noise(rng, n_knots=7):
    """1-D smooth noise on t in [0,1]: cosine-interpolated random knots."""
    knots = [rng.uniform(-1, 1) for _ in range(n_knots + 1)]

    def f(t):
        x = t * n_knots
        i = min(int(x), n_knots - 1)
        u = x - i
        u = (1 - math.cos(u * math.pi)) / 2
        return knots[i] * (1 - u) + knots[i + 1] * u
    return f


def _width(t, base, wn):
    # loaded start: rises fast to ~1.15, eases down, then tapers to 0 over the last 30%
    attack = 0.62 + 0.38 * (1 - math.exp(-t * 30))
    body = 1.12 - 0.22 * t
    tail = 1.0 if t < 0.74 else max(0.0, 1 - ((t - 0.74) / 0.26) ** 1.7)
    return base * attack * body * tail * (1 + 0.10 * wn(t))


def _centre(t, p, rn):
    a = math.radians(p["start"] + p["sweep"] * t)
    r = p["r"] * (1 + p["wobble"] * rn(t))
    # the brush drifts slightly inward at the end, as a hand-drawn circle does
    r -= 0.5 * max(0.0, t - 0.8) / 0.2
    return CX + r * math.cos(a), CY + r * math.sin(a) * 0.985, a


def _fmt(pts):
    return " ".join(f"{x:.{PREC}f} {y:.{PREC}f}" for x, y in pts)


def band_path(p, lo, hi, t_end, rng_seed, t_start=0.0):
    """Outline of the part of the stroke between fractions lo..hi of its width (0 = outer edge, 1 = inner),
    running from t=0 to t=t_end, as a closed path."""
    rng = random.Random(p["seed"])
    rn, wn = _smooth_noise(rng), _smooth_noise(rng)
    jit = random.Random(rng_seed)
    outer, inner = [], []
    for i in range(N + 1):
        t = t_start + (t_end - t_start) * i / N
        x, y, a = _centre(t, p, rn)
        w = _width(t, p["w"], wn)
        nx, ny = math.cos(a), math.sin(a)  # outward normal
        e = 0.05 * w * jit.uniform(-1, 1) if t > 0.62 else 0.0  # ragged edge in the dry part
        o, n_ = w * (0.5 - lo) + e, w * (0.5 - hi)
        outer.append((x + nx * o, y + ny * o))
        inner.append((x + nx * n_, y + ny * n_))
    inner.reverse()
    cap = []
    if lo == 0 and hi == 1 and t_start == 0:  # round, slightly bulbous start where the loaded brush lands
        x, y, a = _centre(0.0, p, rn)
        rc = _width(0.0, p["w"], wn) / 2
        for k in range(1, 12):  # from the inner edge, around the back of the stroke, to the outer edge
            phi = a + math.pi + math.pi * k / 12
            cap.append((x + rc * 1.08 * math.cos(phi), y + rc * 1.08 * math.sin(phi)))
    return f"M{outer[0][0]:.{PREC}f} {outer[0][1]:.{PREC}f}L{_fmt(outer[1:])}L{_fmt(inner)}" + (f"L{_fmt(cap)}" if cap else "") + "Z"


def enso_paths(name):
    p = VARIANTS[name]
    if not p["bristles"]:
        return [band_path(p, 0, 1, 1.0, 1)]
    # the solid body ends around 70%; the tail is split into bristle streaks of different lengths with small gaps
    rng = random.Random(p["seed"] * 31)
    paths = [band_path(p, 0, 1, 0.80, 1)]
    k = p["bristles"]
    # uneven bristle widths and gaps, so the tail reads as dry ink rather than stripes
    cuts = sorted(rng.uniform(0.08, 0.92) for _ in range(k - 1))
    edges = [0.0] + cuts + [1.0]
    for j in range(k):
        g = rng.uniform(0.015, 0.05)
        lo, hi = edges[j] + (g if j else 0.0), edges[j + 1] - (g if j < k - 1 else 0.0)
        t_end = 0.88 + 0.12 * rng.random() if j not in (0, k - 1) else 0.93 + 0.07 * rng.random()
        paths.append(band_path(p, lo, hi, t_end, 100 + j, t_start=0.7))
    return paths


def start_point(name):
    p = VARIANTS[name]
    rng = random.Random(p["seed"])
    rn = _smooth_noise(rng)
    x, y, _ = _centre(0.0, p, rn)
    return x, y


def mix(a, b, f):
    """Mix two #rrggbb colours: f of a, 1-f of b."""
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x * f + y * (1 - f)):02x}" for x, y in zip(ca, cb))


def svg(name, ink="#ece9e2", tint="#5fd6ee", size=96, texture=False, bg="#0e0f13"):
    sx, sy = start_point(name)
    paths = enso_paths(name)
    filt = ""
    fattr = ""
    if texture:
        filt = ('<filter id="dry" x="-10%" y="-10%" width="120%" height="120%">'
                '<feTurbulence type="fractalNoise" baseFrequency="1.6" numOctaves="2" seed="4" result="n"/>'
                '<feDisplacementMap in="SourceGraphic" in2="n" scale="0.35"/></filter>')
        fattr = ' filter="url(#dry)"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" height="{size}" '
            f'style="background:{bg}"><defs>'
            f'<radialGradient id="ink" gradientUnits="userSpaceOnUse" cx="{sx:.2f}" cy="{sy:.2f}" r="14">'
            f'<stop offset="0" stop-color="{mix(tint, ink, 0.6)}"/><stop offset="0.38" stop-color="{ink}"/>'
            f'</radialGradient>{filt}</defs><g fill="url(#ink)"{fattr}>'
            + "".join(f'<path d="{d}"/>' for d in paths) + '</g></svg>')


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "variants"
    if arg == "variants":
        out = Path(__file__).parent / "out"
        out.mkdir(exist_ok=True)
        cells = []
        for name in ("classic", "open", "bold"):
            big = svg(name, tint="#5fd6ee", size=220, texture=True)
            mid = svg(name, tint="#5fd6ee", size=28)
            fav = svg("favicon" if name == "classic" else name, tint="#5fd6ee", size=16)
            cells.append(f'<div class="c"><div>{big}</div><div class="s">{mid}{fav}'
                         f'<span class="w">ZEN&nbsp;<i>HARNESS</i></span></div><p>{name}</p></div>')
        html = ('<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#0e0f13;color:#ece9e2;'
                'font:13px Geist,system-ui,sans-serif;display:flex;gap:28px;padding:28px}'
                '.c{display:flex;flex-direction:column;gap:14px;align-items:center}'
                '.s{display:flex;gap:12px;align-items:center}.w{font:600 12.5px ui-monospace,monospace;'
                'letter-spacing:.18em}.w i{font-style:normal;font-weight:500;color:#8b8a86}p{margin:0;'
                'color:#8b8a86;letter-spacing:.12em;text-transform:uppercase;font-size:11px}</style>'
                + "".join(cells))
        (out / "enso-variants.html").write_text(html, encoding="utf-8")
        print(out / "enso-variants.html")
    elif arg == "ts":                 # src/components/enso-paths.ts (generated; lighter: fewer samples, 1 decimal)
        N, PREC = 72, 1
        sx, sy = start_point("classic")
        fx, fy = start_point("favicon")
        body = enso_paths("classic")
        ts = ("// Generated by scripts/enso.py ts -- the Zen enso (one brush stroke, open at the top right).\n"
              "// ENSO: body + dry-brush bristle paths; ENSO_BOLD: the simpler, heavier stroke for 16-20 px.\n"
              f"export const ENSO = {json.dumps(body)}\n"
              f"export const ENSO_START = [{sx:.1f}, {sy:.1f}] as const\n"
              f"export const ENSO_BOLD = {json.dumps(enso_paths('favicon'))}\n"
              f"export const ENSO_BOLD_START = [{fx:.1f}, {fy:.1f}] as const\n")
        dst = Path(__file__).parent.parent / "src" / "components" / "enso-paths.ts"
        dst.write_text(ts, encoding="utf-8")
        print(dst, len(ts), "bytes")
    elif arg == "favicon":            # public/favicon.svg and the window-icon SVG
        N, PREC = 72, 1
        ink, tint = "#ece9e2", "#e8e4da"
        fx, fy = start_point("favicon")
        def icon(bg):
            return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                    + (f'<rect x="0.5" y="0.5" width="31" height="31" rx="7" fill="{bg}"/>' if bg else "")
                    + f'<defs><radialGradient id="i" gradientUnits="userSpaceOnUse" cx="{fx:.1f}" cy="{fy:.1f}" r="14">'
                    f'<stop offset="0" stop-color="{mix(tint, ink, 0.6)}"/><stop offset="0.38" stop-color="{ink}"/>'
                    f'</radialGradient></defs><path fill="url(#i)" d="{enso_paths("favicon")[0]}"/></svg>\n')
        root = Path(__file__).parent.parent
        (root / "public" / "favicon.svg").write_text(icon("#16181f"), encoding="utf-8")
        (root / "scripts" / "out" / "zen_harness_icon.svg").write_text(icon("#16181f"), encoding="utf-8")
        print("favicon written")
    elif arg == "svg":
        print(svg(sys.argv[2], tint=sys.argv[3] if len(sys.argv) > 3 else "#5fd6ee", size=32, bg="transparent"))
    else:
        print("\n".join(enso_paths(arg)))
