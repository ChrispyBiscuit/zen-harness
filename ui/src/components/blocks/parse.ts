/** The JSON body of an answer block, read leniently: small models write trailing commas, smart quotes, a "json"
 *  line, a stray note after the closing brace, or drop a closing bracket (the live 27B: `"items":[["a","b"]}`).
 *  Each repair is tried only when the text before it doesn't parse. The decode effect's markers go first. */
export function parseBlock(src: string): { ok: true; data: any } | { ok: false; error: string } {
  let s = src.replace(/[]/g, "").trim()
  s = s.replace(/^json\s*\n/i, "")
  const start = s.search(/[[{]/)
  if (start < 0) return { ok: false, error: "no data" }
  const close = s[start] === "[" ? "]" : "}"
  const end = s.lastIndexOf(close)
  if (end < start) return { ok: false, error: "unfinished" }
  s = s.slice(start, end + 1)
  const tries = [
    (x: string) => x,
    (x: string) => x.replace(/,\s*([\]}])/g, "$1"),
    (x: string) => x.replace(/[“”]/g, '"').replace(/[‘’]/g, "'"),
    balance,
  ]
  let err = ""
  for (const fix of tries) {
    s = fix(s)
    try {
      return { ok: true, data: JSON.parse(s) }
    } catch (e: any) {
      err ||= String(e?.message || e).replace(/^JSON\.parse: /, "")
    }
  }
  return { ok: false, error: err }
}

/** Closing brackets put back where a model left one out: a closer that doesn't match the open bracket first closes
 *  what is still open inside it; anything open at the end is closed. Strings are skipped. */
export function balance(s: string): string {
  const stack: string[] = []
  let out = "", inStr = false, esc = false
  for (const ch of s) {
    if (inStr) {
      out += ch
      if (esc) esc = false
      else if (ch === "\\") esc = true
      else if (ch === '"') inStr = false
      continue
    }
    if (ch === '"') inStr = true
    else if (ch === "[" || ch === "{") stack.push(ch === "[" ? "]" : "}")
    else if (ch === "]" || ch === "}") {
      if (stack.includes(ch)) while (stack.length && stack[stack.length - 1] !== ch) out += stack.pop()
      if (stack[stack.length - 1] === ch) stack.pop()
      else continue                                   // a closer for nothing open: dropped
    }
    out += ch
  }
  return out + stack.reverse().join("")
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
    return items.flatMap((it): [string, string][] => {
      // a list of plain points instead of [key, value] pairs (["6 in screen", "IPX8", …]): one row each, no key
      if (typeof it === "string" || typeof it === "number") return [["", str(it)]]
      if (Array.isArray(it)) return it.length > 2 ? it.map((x): [string, string] => ["", str(x)]) : [[str(it[0]), str(it[1])]]
      if (it && typeof it === "object") return [[str(it.label ?? it.key ?? it.name ?? it.k), str(it.value ?? it.v ?? it.text)]]
      return []
    }).filter((x) => !!(x[0] || x[1]))
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
