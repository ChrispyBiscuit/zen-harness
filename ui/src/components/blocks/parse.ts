/** The JSON body of an answer block, read leniently: small models write trailing commas, smart quotes, a "json"
 *  line or a stray note after the closing brace. The decode effect's markers are removed first. */
export function parseBlock(src: string): { ok: true; data: any } | { ok: false; error: string } {
  let s = src.replace(/[\uE000\uE001]/g, "").trim()
  s = s.replace(/^json\s*\n/i, "")
  s = s.replace(/[“”]/g, '"').replace(/[‘’]/g, "'")
  const start = s.search(/[[{]/)
  if (start < 0) return { ok: false, error: "no data" }
  const close = s[start] === "[" ? "]" : "}"
  const end = s.lastIndexOf(close)
  if (end < start) return { ok: false, error: "unfinished" }
  s = s.slice(start, end + 1).replace(/,\s*([\]}])/g, "$1")
  try {
    return { ok: true, data: JSON.parse(s) }
  } catch (e: any) {
    return { ok: false, error: String(e?.message || e).replace(/^JSON\.parse: /, "") }
  }
}

/** A number from a value a model may have written as text ("1,200", "$4.5", "12%"). */
export function num(v: any): number | null {
  if (typeof v === "number") return isFinite(v) ? v : null
  if (typeof v !== "string") return null
  const m = /-?\d[\d,]*(?:\.\d+)?|-?\.\d+/.exec(v.replace(/−/g, "-"))
  if (!m) return null
  const n = parseFloat(m[0].replace(/,/g, ""))
  return isFinite(n) ? n : null
}

export const str = (v: any) => (v == null ? "" : typeof v === "string" ? v : typeof v === "number" ? String(v) : JSON.stringify(v))

/** [["k","v"]] or {k: v} or [{label|key|name, value}] → [k, v][] */
export function pairs(items: any): [string, string][] {
  if (!items) return []
  if (Array.isArray(items)) {
    return items.map((it): [string, string] | null => {
      if (Array.isArray(it)) return [str(it[0]), str(it[1])]
      if (it && typeof it === "object") return [str(it.label ?? it.key ?? it.name ?? it.k), str(it.value ?? it.v ?? it.text)]
      return null
    }).filter((x): x is [string, string] => !!x && !!(x[0] || x[1]))
  }
  if (typeof items === "object") return Object.entries(items).map(([k, v]) => [k, str(v)])
  return []
}

/** Compact number: 1234 → 1.23k, 4500000 → 4.5M; small numbers keep up to 2 decimals. */
export function fmt(n: number, unit = ""): string {
  const a = Math.abs(n)
  let s: string
  if (a >= 1e12) s = trim(n / 1e12) + "T"
  else if (a >= 1e9) s = trim(n / 1e9) + "B"
  else if (a >= 1e6) s = trim(n / 1e6) + "M"
  else if (a >= 1e4) s = trim(n / 1e3) + "k"
  else s = n.toLocaleString("en-US", { maximumFractionDigits: a < 1 ? 3 : 2 })
  if (!unit) return s
  if (/^[$€£¥₹]/.test(unit)) return (n < 0 ? "-" : "") + unit + s.replace(/^-/, "")
  return unit === "%" ? s + "%" : s + " " + unit
}
const trim = (x: number) => x.toFixed(Math.abs(x) >= 100 ? 0 : Math.abs(x) >= 10 ? 1 : 2).replace(/\.?0+$/, "")

/** Exact value for tables and tooltips. */
export const exact = (n: number, unit = "") => {
  const s = n.toLocaleString("en-US", { maximumFractionDigits: 4 })
  if (!unit) return s
  if (/^[$€£¥₹]/.test(unit)) return (n < 0 ? "-" : "") + unit + s.replace(/^-/, "")
  return unit === "%" ? s + "%" : s + " " + unit
}
