// Windows paths in model output become clickable file links (opened in the side panel).

const PATH = String.raw`[A-Za-z]:[\\/](?:[^\s<>"'|?*\x60\[\]()]*[^\s<>"'|?*\x60\[\]().,;:!])?`
export const PATH_FULL = new RegExp(`^${PATH}$`)
const BARE = new RegExp(`(^|[\\s(])(${PATH})`, "g")

export const isWinPath = (s: string) => PATH_FULL.test(s.trim())

export function toFileHref(p: string) {
  return "file:///" + encodeURI(p.replace(/\\/g, "/")).replace(/#/g, "%23")
}

export function fromHref(href: string): string | null {
  let h = href
  if (/^file:\/\//i.test(h)) h = h.replace(/^file:\/\/\/?/i, "")
  try {
    h = decodeURI(h)
  } catch {
    /* keep as is */
  }
  return /^[A-Za-z]:[\\/]/.test(h) ? h.replace(/\//g, "\\") : null
}

export const basename = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p
export const dirname = (p: string) => p.replace(/[\\/][^\\/]*$/, "")

/** Wrap bare Windows paths in markdown links, leaving code spans/blocks and existing links alone. */
export function linkifyPaths(md: string): string {
  const parts = md.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`|\[[^\]]*\]\([^)]*\))/g)
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part
      return part.replace(BARE, (_m, pre: string, p: string) => `${pre}[${p}](<${toFileHref(p)}>)`)
    })
    .join("")
}

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function langFor(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase()
  const map: Record<string, string> = {
    py: "python", js: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx", json: "json", md: "markdown",
    ps1: "powershell", psm1: "powershell", bat: "bat", cmd: "bat", sh: "bash", html: "html", htm: "html",
    css: "css", yml: "yaml", yaml: "yaml", toml: "toml", ini: "ini", xml: "xml", sql: "sql", cs: "csharp",
    java: "java", go: "go", rs: "rust", c: "c", h: "c", cpp: "cpp", rb: "ruby", php: "php", txt: "text", log: "log",
  }
  return map[ext] || "text"
}
