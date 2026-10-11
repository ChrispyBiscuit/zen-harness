import { useEffect, useState } from "react"
import { Check, Copy } from "lucide-react"
import { cn } from "@/lib/utils"

const cache = new Map<string, string>()

// A fixed set of languages keeps the bundle small (all of Shiki is ~300 files / 13 MB).
const LANGS: Record<string, () => Promise<any>> = {
  python: () => import("shiki/langs/python.mjs"), javascript: () => import("shiki/langs/javascript.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"), tsx: () => import("shiki/langs/tsx.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"), json: () => import("shiki/langs/json.mjs"),
  powershell: () => import("shiki/langs/powershell.mjs"), bash: () => import("shiki/langs/bash.mjs"),
  bat: () => import("shiki/langs/bat.mjs"), markdown: () => import("shiki/langs/markdown.mjs"),
  html: () => import("shiki/langs/html.mjs"), css: () => import("shiki/langs/css.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"), toml: () => import("shiki/langs/toml.mjs"),
  sql: () => import("shiki/langs/sql.mjs"), csharp: () => import("shiki/langs/csharp.mjs"),
  java: () => import("shiki/langs/java.mjs"), go: () => import("shiki/langs/go.mjs"),
  rust: () => import("shiki/langs/rust.mjs"), c: () => import("shiki/langs/c.mjs"),
  cpp: () => import("shiki/langs/cpp.mjs"), xml: () => import("shiki/langs/xml.mjs"),
  diff: () => import("shiki/langs/diff.mjs"), ini: () => import("shiki/langs/ini.mjs"),
  dockerfile: () => import("shiki/langs/dockerfile.mjs"),
}
const ALIAS: Record<string, string> = { ps: "powershell", ps1: "powershell", pwsh: "powershell", sh: "bash", shell: "bash",
  zsh: "bash", console: "bash", py: "python", js: "javascript", ts: "typescript", yml: "yaml", "c++": "cpp", cs: "csharp",
  "c#": "csharp", md: "markdown", cmd: "bat", batch: "bat", rs: "rust", golang: "go", patch: "diff", htm: "html" }

let core: Promise<any> | null = null
function getCore() {
  core ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }, theme] = await Promise.all([
      import("shiki/core"), import("shiki/engine/javascript"), import("shiki/themes/github-dark-default.mjs")])
    return createHighlighterCore({ themes: [theme.default], langs: [], engine: createJavaScriptRegexEngine() })
  })()
  return core
}

export async function highlight(code: string, lang: string): Promise<string | null> {
  const l = ALIAS[lang?.toLowerCase()] || lang?.toLowerCase() || ""
  const key = `${l}\u0000${code}`
  if (cache.has(key)) return cache.get(key)!
  if (code.length > 60000 || !LANGS[l]) return null
  try {
    const h = await getCore()
    if (!h.getLoadedLanguages().includes(l)) await h.loadLanguage((await LANGS[l]()).default)
    const html = h.codeToHtml(code, { lang: l, theme: "github-dark-default" })
    cache.set(key, html)
    return html
  } catch {
    return null
  }
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-meta text-muted-foreground hover:bg-white/5 hover:text-foreground", className)}
      onClick={() => {
        navigator.clipboard.writeText(text)
        setDone(true)
        setTimeout(() => setDone(false), 1200)
      }}
    >
      {done ? <Check className="size-3" /> : <Copy className="size-3" />}
      {done ? "Copied" : "Copy"}
    </button>
  )
}

export function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    highlight(code, lang).then((h) => alive && setHtml(h))
    return () => {
      alive = false
    }
  }, [code, lang])
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg border border-border bg-code">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1">
        <span className="mono text-meta text-muted-foreground">{lang || "code"}</span>
        <CopyButton text={code} className="opacity-0 transition-opacity group-hover/code:opacity-100" />
      </div>
      {html ? (
        <div className="mono overflow-x-auto px-4 py-3 text-ui leading-relaxed [&_pre]:!m-0" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="mono overflow-x-auto px-4 py-3 text-ui leading-relaxed text-foreground/90">{code}</pre>
      )}
    </div>
  )
}
