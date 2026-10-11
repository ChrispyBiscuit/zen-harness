// Bundle size per chunk (raw and gzip) of a built dist folder: node scripts/bundle-size.mjs <dist>
import fs from "node:fs"
import path from "node:path"
import zlib from "node:zlib"

const dist = process.argv[2] || path.resolve(import.meta.dirname, "../../backend/web_dist")
const dir = path.join(dist, "assets")
const rows = fs.readdirSync(dir).filter((f) => /\.(js|css)$/.test(f)).map((f) => {
  const b = fs.readFileSync(path.join(dir, f))
  return { file: f, kb: +(b.length / 1024).toFixed(1), gzipKb: +(zlib.gzipSync(b, { level: 9 }).length / 1024).toFixed(1) }
}).sort((a, b) => b.gzipKb - a.gzipKb)
const sum = (k, ext) => +rows.filter((r) => r.file.endsWith(ext)).reduce((s, r) => s + r[k], 0).toFixed(1)
console.log(JSON.stringify({ chunks: rows.length, jsKb: sum("kb", ".js"), jsGzipKb: sum("gzipKb", ".js"),
  cssKb: sum("kb", ".css"), cssGzipKb: sum("gzipKb", ".css"), top: rows.slice(0, 12) }, null, 2))
