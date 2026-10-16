import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Frame, ToggleButton } from "./Frame"
import { exact, fmt, num, str } from "./parse"

/* A small SVG chart for ```chart blocks: bar (vertical, or horizontal when the labels are long), line, area, donut
   and scatter. Dataviz rules: thin marks, 4px rounded data ends on the baseline, 2px lines, a hairline grid, one
   y axis, a legend for 2+ series, text in ink (never the series colour), hover tooltips and a table view.
   Colours are --series-1..8 (index.css), validated against the card surface; a 9th series never gets a colour. */

type Kind = "bar" | "hbar" | "line" | "area" | "donut" | "scatter"
type Series = { name: string; data: (number | null)[] }
type Pt = { x: number; y: number; label?: string }
export type ChartSpec = { kind: Kind; title: string; unit: string; x: string[]; series: Series[]; points: Pt[][]; note: string }

const MAX_SERIES = 8
const color = (i: number) => `var(--series-${(i % MAX_SERIES) + 1})`

export function normalizeChart(d: any): ChartSpec | null {
  if (!d || typeof d !== "object") return null
  if (Array.isArray(d)) d = { data: d }
  const t = String(d.type || d.kind || "").toLowerCase()
  let x: string[] = Array.isArray(d.x) ? d.x.map(str) : Array.isArray(d.labels) ? d.labels.map(str) : Array.isArray(d.categories) ? d.categories.map(str) : []
  let series: Series[] = []
  const points: Pt[][] = []
  const rawSeries: any[] = Array.isArray(d.series) ? d.series : Array.isArray(d.datasets) ? d.datasets : []
  if (rawSeries.length) {
    rawSeries.forEach((s: any, i: number) => {
      const arr: any[] = Array.isArray(s) ? s : Array.isArray(s?.data) ? s.data : Array.isArray(s?.values) ? s.values : []
      const name = str(s?.name ?? s?.label ?? `Series ${i + 1}`)
      if (arr.length && typeof arr[0] === "object" && arr[0] !== null) {      // [{label, value}] / [[x, y]] / [{x, y}]
        const pts: Pt[] = arr.map((p: any) => Array.isArray(p) ? { x: num(p[0]) ?? NaN, y: num(p[1]) ?? NaN, label: str(p[0]) }
          : { x: num(p.x) ?? NaN, y: num(p.y ?? p.value) ?? NaN, label: str(p.label ?? p.name ?? p.x) })
        points.push(pts)
        if (!x.length) x = pts.map((p) => p.label || "")
        series.push({ name, data: pts.map((p) => (isFinite(p.y) ? p.y : null)) })
      } else {
        series.push({ name, data: arr.map(num) })
      }
    })
  } else if (Array.isArray(d.data)) {                                           // "data": [{label, value}] / [[k, v]]
    const rows = d.data
    if (rows.length && typeof rows[0] === "number") series = [{ name: str(d.title || "Value"), data: rows.map(num) }]
    else {
      x = rows.map((r: any) => str(Array.isArray(r) ? r[0] : r?.label ?? r?.name ?? r?.x ?? r?.key))
      const vals = rows.map((r: any) => num(Array.isArray(r) ? r[1] : r?.value ?? r?.y ?? r?.v))
      series = [{ name: str(d.title || d.name || "Value"), data: vals }]
      if (t === "scatter") points.push(rows.map((r: any) => ({ x: num(Array.isArray(r) ? r[0] : r?.x) ?? NaN, y: num(Array.isArray(r) ? r[1] : r?.y ?? r?.value) ?? NaN, label: str(r?.label ?? r?.name) })))
    }
  }
  series = series.filter((s) => s.data.some((v) => v != null))
  if (!series.length) return null
  const n = Math.max(...series.map((s) => s.data.length))
  if (x.length < n) x = [...x, ...Array.from({ length: n - x.length }, (_, i) => String(x.length + i + 1))]
  let note = ""
  if (series.length > MAX_SERIES) { note = `Showing the first ${MAX_SERIES} of ${series.length} series.`; series = series.slice(0, MAX_SERIES) }
  const longLabels = Math.max(...x.map((l) => l.length)) > 10
  const timeLike = x.length >= 6 && x.every((l) => /^(\d{4}|q[1-4]|\d{4}[-/ ]|[A-Z][a-z]{2}\b)/i.test(l))
  let kind: Kind
  if (/pie|donut|doughnut/.test(t)) kind = "donut"
  else if (/scatter|bubble/.test(t)) kind = "scatter"
  else if (/area/.test(t)) kind = "area"
  else if (/line/.test(t)) kind = "line"
  else if (/hbar|horizontal/.test(t)) kind = "hbar"
  else if (/bar|column|histogram/.test(t)) kind = longLabels && !/column/.test(t) ? "hbar" : "bar"
  else kind = timeLike ? "line" : longLabels ? "hbar" : "bar"
  if (kind === "scatter" && !points.length) kind = "line"
  if (kind === "donut") {                                  // one series; slices past 7 fold into "Other"
    const s = series[0]
    const rows = x.map((l, i) => ({ l, v: Math.max(0, s.data[i] ?? 0) })).filter((r) => r.v > 0).sort((a, b) => b.v - a.v)
    if (rows.length > MAX_SERIES) { const rest = rows.splice(MAX_SERIES - 1); rows.push({ l: "Other", v: rest.reduce((a, r) => a + r.v, 0) }) }
    x = rows.map((r) => r.l); series = [{ name: s.name, data: rows.map((r) => r.v) }]
  }
  return { kind, title: str(d.title), unit: str(d.unit || d.units || ""), x, series, points: points.slice(0, MAX_SERIES), note }
}

function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (lo === hi) { hi = lo === 0 ? 1 : lo + Math.abs(lo) * 0.5; lo = Math.min(0, lo) }
  const raw = (hi - lo) / count
  const p = Math.pow(10, Math.floor(Math.log10(raw)))
  const e = raw / p
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * p
  const out: number[] = []
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.5; v += step) out.push(+v.toPrecision(12))
  if (out[out.length - 1] < hi) out.push(+(out[out.length - 1] + step).toPrecision(12))
  return out
}

/** A bar with its data end rounded (4px) and its base square on the axis. */
function bar(x: number, y: number, w: number, h: number, end: "top" | "bottom" | "right" | "left") {
  if (w <= 0 || h <= 0) return ""
  const r = Math.min(4, (end === "top" || end === "bottom" ? w : h) / 2, end === "top" || end === "bottom" ? h : w)
  if (end === "top") return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
  if (end === "bottom") return `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`
  if (end === "right") return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`
  return `M${x + w},${y}H${x + r}Q${x},${y} ${x},${y + r}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w}Z`
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

const charW = 6.4                                    // ~ px per character of 10.5px Geist, for label room
const AX = "fill-faint text-[10.5px] tabular-nums"

type Tip = { x: number; y: number; head: string; rows: { c: string; k: string; v: string }[] } | null

function Tooltip({ tip, width }: { tip: Tip; width: number }) {
  if (!tip) return null
  const left = Math.min(Math.max(tip.x + 12, 0), Math.max(0, width - 180))
  return (
    <div className="pointer-events-none absolute z-10 min-w-32 max-w-60 rounded-[var(--radius-chip)] bg-popover px-2.5 py-1.5 text-meta shadow-elev-2 ring-1 ring-line-2"
      style={{ left, top: Math.max(0, tip.y - 8), transform: "translateY(-100%)" }}>
      {tip.head && <div className="mb-0.5 truncate font-medium text-foreground">{tip.head}</div>}
      {tip.rows.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5 text-muted-foreground">
          <span className="size-2 shrink-0 rounded-[2px]" style={{ background: r.c }} />
          <span className="min-w-0 flex-1 truncate">{r.k}</span>
          <span className="tabular-nums text-foreground">{r.v}</span>
        </div>
      ))}
    </div>
  )
}

function Legend({ spec }: { spec: ChartSpec }) {
  const names = spec.kind === "donut" ? [] : spec.series.map((s) => s.name)
  if (names.length < 2) return null
  return (
    <div className="mb-1.5 flex flex-wrap gap-x-3 gap-y-1 text-meta text-muted-foreground">
      {names.map((n, i) => (
        <span key={i} className="flex items-center gap-1.5"><span className="size-2 rounded-[2px]" style={{ background: color(i) }} />{n}</span>
      ))}
    </div>
  )
}

function Cartesian({ spec, width }: { spec: ChartSpec; width: number }) {
  const [tip, setTip] = useState<Tip>(null)
  const [hover, setHover] = useState<number | null>(null)
  const { kind, x, series, unit } = spec
  const n = x.length
  const vals = series.flatMap((s) => s.data.filter((v): v is number => v != null))
  const isBar = kind === "bar" || kind === "hbar"
  let lo = Math.min(...vals), hi = Math.max(...vals)
  if (isBar || kind === "area") { lo = Math.min(0, lo); hi = Math.max(0, hi) }
  else { const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1; lo = lo >= 0 && lo - pad < 0 ? 0 : lo - pad; hi = hi + pad }
  const ticks = niceTicks(lo, hi)
  const t0 = ticks[0], t1 = ticks[ticks.length - 1]
  const tickW = Math.max(...ticks.map((t) => fmt(t, unit).length)) * charW + 10

  if (kind === "hbar") {
    const labW = Math.min(150, Math.max(...x.map((l) => l.length)) * charW + 8)
    const band = series.length > 1 ? 10 * series.length + 10 : 26
    const H = n * band + 22
    const L = labW, R = 44, plotW = Math.max(40, width - L - R)
    const sx = (v: number) => L + ((v - t0) / (t1 - t0)) * plotW
    const bw = (band - 8 - (series.length - 1) * 2) / series.length
    return (
      <div className="relative" onMouseLeave={() => { setTip(null); setHover(null) }}>
        <svg width={width} height={H} role="img" aria-label={spec.title || "bar chart"}>
          {ticks.map((t) => <g key={t}>
            <line x1={sx(t)} x2={sx(t)} y1={0} y2={n * band} className="stroke-line-1" strokeWidth={1} />
            <text x={sx(t)} y={n * band + 15} textAnchor="middle" className={AX}>{fmt(t, unit)}</text>
          </g>)}
          {x.map((l, i) => (
            <g key={i} onMouseMove={(e) => {
              const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()
              setHover(i)
              setTip({ x: e.clientX - r.left, y: e.clientY - r.top, head: l, rows: series.map((s, j) => ({ c: color(j), k: s.name, v: s.data[i] == null ? "–" : exact(s.data[i]!, unit) })) })
            }}>
              <rect x={0} y={i * band} width={width} height={band} fill="transparent" />
              <text x={L - 8} y={i * band + band / 2} dy="0.35em" textAnchor="end" className="fill-muted-foreground text-[11px]">
                {l.length > 24 ? l.slice(0, 23) + "…" : l}
              </text>
              {series.map((s, j) => {
                const v = s.data[i]
                if (v == null) return null
                const a = sx(Math.min(0, v) < t0 ? t0 : Math.min(0, v)), b = sx(Math.max(0, v))
                const y = i * band + 4 + j * (bw + 2)
                const d = v >= 0 ? bar(sx(Math.max(0, t0)), y, b - sx(Math.max(0, t0)), bw, "right") : bar(a, y, sx(0) - a, bw, "left")
                return <path key={j} d={d} fill={color(j)} opacity={hover == null || hover === i ? 1 : 0.55} />
              })}
              {series.length === 1 && series[0].data[i] != null && (
                <text x={sx(Math.max(0, series[0].data[i]!)) + 5} y={i * band + band / 2} dy="0.35em" className={AX}>{fmt(series[0].data[i]!, unit)}</text>
              )}
            </g>
          ))}
        </svg>
        <Tooltip tip={tip} width={width} />
      </div>
    )
  }

  const H = 220, T = 10, B = 24, L = tickW, R = kind === "bar" ? 8 : 16
  const plotW = Math.max(40, width - L - R), plotH = H - T - B
  const sy = (v: number) => T + plotH - ((v - t0) / (t1 - t0)) * plotH
  const step = plotW / Math.max(1, isBar ? n : n - 1)
  const cx = (i: number) => (isBar ? L + step * (i + 0.5) : L + step * i)
  const every = Math.max(1, Math.ceil((Math.max(...x.map((l) => l.length)) * charW + 10) / step))
  const groupW = Math.min(step * 0.72, 22 * series.length + 2 * (series.length - 1) + 30)
  const bw = Math.max(2, (groupW - (series.length - 1) * 2) / series.length)
  const move = (e: React.MouseEvent<SVGElement>) => {
    const r = (e.currentTarget.ownerSVGElement || (e.currentTarget as SVGSVGElement)).getBoundingClientRect()
    const px = e.clientX - r.left
    const i = Math.max(0, Math.min(n - 1, Math.round(isBar ? (px - L) / step - 0.5 : (px - L) / step)))
    setHover(i)
    setTip({ x: cx(i), y: Math.min(...series.map((s) => (s.data[i] == null ? H : sy(s.data[i]!)))), head: x[i],
      rows: series.map((s, j) => ({ c: color(j), k: s.name, v: s.data[i] == null ? "–" : exact(s.data[i]!, unit) })) })
  }
  const line = (s: Series) => {
    let d = "", pen = false
    s.data.forEach((v, i) => { if (v == null) { pen = false; return } d += `${pen ? "L" : "M"}${cx(i)},${sy(v)}`; pen = true })
    return d
  }
  const base = sy(Math.max(t0, Math.min(0, t1)))
  return (
    <div className="relative" onMouseLeave={() => { setTip(null); setHover(null) }}>
      <svg width={width} height={H} role="img" aria-label={spec.title || `${kind} chart`}>
        {ticks.map((t) => <g key={t}>
          <line x1={L} x2={L + plotW} y1={sy(t)} y2={sy(t)} className={t === 0 ? "stroke-line-3" : "stroke-line-1"} strokeWidth={1} />
          <text x={L - 6} y={sy(t)} dy="0.35em" textAnchor="end" className={AX}>{fmt(t, unit)}</text>
        </g>)}
        {x.map((l, i) => (i % every === 0 || i === n - 1 && (n - 1) % every > every / 2) ? (
          <text key={i} x={cx(i)} y={H - 7} textAnchor={!isBar && i === 0 ? "start" : !isBar && i === n - 1 ? "end" : "middle"} className={AX}>
            {l.length > 14 ? l.slice(0, 13) + "…" : l}
          </text>) : null)}
        {isBar ? x.map((_, i) => series.map((s, j) => {
          const v = s.data[i]
          if (v == null) return null
          const x0 = cx(i) - groupW / 2 + j * (bw + 2)
          const d = v >= 0 ? bar(x0, sy(v), bw, base - sy(v), "top") : bar(x0, base, bw, sy(v) - base, "bottom")
          return <path key={`${i}-${j}`} d={d} fill={color(j)} opacity={hover == null || hover === i ? 1 : 0.55} />
        })) : series.map((s, j) => (
          <g key={j}>
            {kind === "area" && <path d={line(s) + `L${cx(s.data.length - 1)},${base}L${cx(s.data.findIndex((v) => v != null))},${base}Z`}
              fill={color(j)} opacity={0.14} />}
            <path d={line(s)} fill="none" stroke={color(j)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {n <= 16 && s.data.map((v, i) => v == null ? null :
              <circle key={i} cx={cx(i)} cy={sy(v)} r={hover === i ? 4.5 : 3} fill={color(j)} className="stroke-surface-1" strokeWidth={1.5} />)}
          </g>
        ))}
        {!isBar && hover != null && <line x1={cx(hover)} x2={cx(hover)} y1={T} y2={T + plotH} className="stroke-line-3" strokeWidth={1} strokeDasharray="3 3" />}
        {isBar && series.length === 1 && n <= 8 && series[0].data.map((v, i) => v == null ? null :
          <text key={i} x={cx(i)} y={v >= 0 ? sy(v) - 5 : sy(v) + 12} textAnchor="middle" className={AX}>{fmt(v, unit)}</text>)}
        <rect x={L} y={T} width={plotW} height={plotH} fill="transparent" onMouseMove={move} />
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  )
}

function Donut({ spec, width }: { spec: ChartSpec; width: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const s = spec.series[0]
  const total = s.data.reduce<number>((a, v) => a + (v ?? 0), 0) || 1
  const size = Math.min(180, Math.max(120, width * 0.38)), r = size / 2, ri = r * 0.62
  let a0 = -Math.PI / 2
  const arcs = s.data.map((v, i) => {
    const a = ((v ?? 0) / total) * Math.PI * 2, a1 = a0 + a
    const big = a > Math.PI ? 1 : 0
    const p = (rad: number, ang: number) => `${r + rad * Math.cos(ang)},${r + rad * Math.sin(ang)}`
    const d = a >= Math.PI * 2 - 1e-6
      ? `M${p(r, 0)}A${r},${r} 0 1 1 ${p(r, Math.PI)}A${r},${r} 0 1 1 ${p(r, 0)}M${p(ri, 0)}A${ri},${ri} 0 1 0 ${p(ri, Math.PI)}A${ri},${ri} 0 1 0 ${p(ri, 0)}Z`
      : `M${p(r, a0)}A${r},${r} 0 ${big} 1 ${p(r, a1)}L${p(ri, a1)}A${ri},${ri} 0 ${big} 0 ${p(ri, a0)}Z`
    a0 = a1
    return d
  })
  const pct = spec.unit === "%"                      // values already shares: no second percent column, no "total"
  const h = hover ?? (pct ? 0 : -1)
  const stacked = width < 360
  return (
    <div className={stacked ? "flex flex-col items-center gap-3" : "flex items-center gap-5"} onMouseLeave={() => setHover(null)}>
      <svg width={size} height={size} role="img" aria-label={spec.title || "donut chart"} className="shrink-0" fillRule="evenodd">
        {arcs.map((d, i) => <path key={i} d={d} fill={color(i)} className="stroke-surface-1" strokeWidth={2}
          opacity={h < 0 || h === i ? 1 : 0.45} onMouseEnter={() => setHover(i)} />)}
        <text x={r} y={r - 4} textAnchor="middle" className="fill-foreground text-[15px] font-semibold tabular-nums">
          {h >= 0 ? `${Math.round(((s.data[h] ?? 0) / total) * 100)}%` : fmt(total, spec.unit)}
        </text>
        <text x={r} y={r + 13} textAnchor="middle" className="fill-faint text-[10.5px]">
          {h >= 0 ? (spec.x[h].length > 16 ? spec.x[h].slice(0, 15) + "…" : spec.x[h]) : "total"}
        </text>
      </svg>
      <div className="min-w-0 flex-1 space-y-1 self-stretch text-meta">
        {spec.x.map((l, i) => (
          <div key={i} onMouseEnter={() => setHover(i)}
            className={"flex items-center gap-2 rounded px-1.5 py-0.5 " + (h === i ? "bg-surface-2" : "")}>
            <span className="size-2 shrink-0 rounded-[2px]" style={{ background: color(i) }} />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">{l}</span>
            <span className="tabular-nums text-foreground">{exact(s.data[i] ?? 0, spec.unit)}</span>
            {!pct && <span className="w-9 text-right tabular-nums text-faint">{Math.round(((s.data[i] ?? 0) / total) * 100)}%</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function Scatter({ spec, width }: { spec: ChartSpec; width: number }) {
  const [tip, setTip] = useState<Tip>(null)
  const pts = spec.points.flat().filter((p) => isFinite(p.x) && isFinite(p.y))
  if (!pts.length) return null
  const pad = (lo: number, hi: number) => { const d = (hi - lo) * 0.08 || 1; return [lo - d, hi + d] }
  const [xl, xh] = pad(Math.min(...pts.map((p) => p.x)), Math.max(...pts.map((p) => p.x)))
  const [yl, yh] = pad(Math.min(...pts.map((p) => p.y)), Math.max(...pts.map((p) => p.y)))
  const xt = niceTicks(xl, xh), yt = niceTicks(yl, yh)
  const H = 230, T = 10, B = 24, L = Math.max(...yt.map((t) => fmt(t, spec.unit).length)) * charW + 10, R = 12
  const pw = width - L - R, ph = H - T - B
  const sx = (v: number) => L + ((v - xt[0]) / (xt[xt.length - 1] - xt[0])) * pw
  const sy = (v: number) => T + ph - ((v - yt[0]) / (yt[yt.length - 1] - yt[0])) * ph
  return (
    <div className="relative" onMouseLeave={() => setTip(null)}>
      <svg width={width} height={H} role="img" aria-label={spec.title || "scatter chart"}>
        {yt.map((t) => <g key={"y" + t}><line x1={L} x2={L + pw} y1={sy(t)} y2={sy(t)} className="stroke-line-1" />
          <text x={L - 6} y={sy(t)} dy="0.35em" textAnchor="end" className={AX}>{fmt(t, spec.unit)}</text></g>)}
        {xt.map((t) => <text key={"x" + t} x={sx(t)} y={H - 7} textAnchor="middle" className={AX}>{fmt(t)}</text>)}
        {spec.points.map((ps, j) => ps.map((p, i) => !isFinite(p.x) || !isFinite(p.y) ? null : (
          <circle key={`${j}-${i}`} cx={sx(p.x)} cy={sy(p.y)} r={4.5} fill={color(j)} className="stroke-surface-1" strokeWidth={2}
            onMouseEnter={() => setTip({ x: sx(p.x), y: sy(p.y), head: p.label || "", rows: [{ c: color(j), k: spec.series[j]?.name || "", v: `${fmt(p.x)}, ${exact(p.y, spec.unit)}` }] })} />
        )))}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  )
}

function DataTable({ spec }: { spec: ChartSpec }) {
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-meta">
        <thead><tr className="text-left text-faint">
          <th className="sticky top-0 bg-surface-1 py-1 pr-3 font-medium">{spec.kind === "scatter" ? "Point" : ""}</th>
          {spec.series.map((s, j) => <th key={j} className="sticky top-0 bg-surface-1 py-1 pl-3 text-right font-medium">{s.name}</th>)}
        </tr></thead>
        <tbody>{spec.x.map((l, i) => (
          <tr key={i} className="border-t border-line-1">
            <td className="py-1 pr-3 text-muted-foreground">{l}</td>
            {spec.series.map((s, j) => <td key={j} className="py-1 pl-3 text-right tabular-nums text-foreground">{s.data[i] == null ? "–" : exact(s.data[i]!, spec.unit)}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export const Chart = memo(function Chart({ spec }: { spec: ChartSpec }) {
  const [table, setTable] = useState(false)
  const [ref, width] = useWidth()
  const [shown, setShown] = useState(false)                       // marks grow in once, on first sight
  useEffect(() => { const t = requestAnimationFrame(() => setShown(true)); return () => cancelAnimationFrame(t) }, [])
  const body = useMemo(() => {
    if (!width) return null
    if (spec.kind === "donut") return <Donut spec={spec} width={width} />
    if (spec.kind === "scatter") return <Scatter spec={spec} width={width} />
    return <Cartesian spec={spec} width={width} />
  }, [spec, width])
  return (
    <Frame title={spec.title || undefined}
      actions={<ToggleButton on={table} onClick={() => setTable((v) => !v)} label="Show the numbers as a table">Table</ToggleButton>}>
      <Legend spec={spec} />
      <div ref={ref} className={"chart-body transition-opacity duration-(--dur-slow) " + (shown ? "opacity-100" : "opacity-0")}>
        {table ? <DataTable spec={spec} /> : body}
      </div>
      {spec.note && <div className="mt-1 text-meta text-faint">{spec.note}</div>}
    </Frame>
  )
})
