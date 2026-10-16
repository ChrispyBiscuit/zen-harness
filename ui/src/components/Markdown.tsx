import { memo } from "react"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import "katex/contrib/copy-tex"
import { FileText, Folder } from "lucide-react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import { CodeBlock } from "./CodeBlock"
import { basename, fromHref, isWinPath, linkifyPaths } from "@/lib/paths"
import { useApp } from "@/lib/store"
import { showPanel } from "@/lib/dock"
import { cn } from "@/lib/utils"

function FileLink({ path, children }: { path: string; children?: React.ReactNode }) {
  const openFile = useApp((s) => s.openFile)
  // a folder (trailing slash, or no extension) opens in Explorer; the file viewer only shows files
  const folder = /[\\/]$/.test(path) || !/\.[A-Za-z0-9]{1,8}$/.test(basename(path))
  return (
    <button
      onClick={() => folder
        ? api.open(path.replace(/[\\/]+$/, ""), "default").catch((e: any) => toast.error(`Can't open ${path}: ${e.message}`))
        : openFile(path)}
      title={path}
      className="inline-flex max-w-full items-baseline gap-1 rounded px-1 align-baseline link-ink underline-offset-2 hover:bg-surface-2"
    >
      {folder ? <Folder className="size-3.5 shrink-0 translate-y-0.5" /> : <FileText className="size-3.5 shrink-0 translate-y-0.5" />}
      <span className="truncate">{children ?? basename(path)}</span>
    </button>
  )
}

/** Normalize math delimiters for remark-math: \( \) and \[ \] become $ / $$, and a $ that starts a money
 *  amount ("$25", "$1,200.50") is escaped so it is not read as the start of a formula. Code is left alone. */
export function prepMath(text: string) {
  return text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g).map((part, i) => {
    if (i % 2) return part
    return part
      .replace(/\\\[([\s\S]+?)\\\]/g, (_m, x) => `\n$$\n${x.trim()}\n$$\n`)
      .replace(/\\\(([\s\S]+?)\\\)/g, (_m, x) => `$${x.trim()}$`)
      // a tall formula (fraction, limit, sum, integral) alone on its line or list item → display math, so it
      // gets its own line instead of being squeezed between text lines
      .replace(/^([ \t]*(?:[-*+]|\d+[.)])?[ \t]*(?:\*\*[^*\n]{1,60}\*\*:?[ \t]*)?)\$([^$\n]*\\(?:d?frac|lim|int|sum|prod)[^$\n]*)\$[ \t]*$/gm,
        (_m, lead: string, x: string) => (lead.trim() ? `${lead}\n\n   $$\n   ${x.trim()}\n   $$\n` : `\n$$\n${x.trim()}\n$$\n`))
      .replace(/(^|[^\\$])\$(?=\d[\d,]*(?:\.\d+)?(?:[kKmMbB]n?)?(?:[\s).,;:!?%/]|$))/g, "$1\\$")
  }).join("")
}

export const SCR_OPEN = "\uE000", SCR_CLOSE = "\uE001"

/** rehype plugin: text between SCR_OPEN and SCR_CLOSE becomes <span class="scramble"> (the decode effect). */
function rehypeScramble() {
  const walk = (node: any) => {
    if (!node.children) return
    const out: any[] = []
    for (const ch of node.children) {
      if (ch.type === "text" && typeof ch.value === "string" && ch.value.includes(SCR_OPEN)) {
        const [before, rest] = ch.value.split(SCR_OPEN)
        const [inside, after = ""] = rest.split(SCR_CLOSE)
        if (before) out.push({ type: "text", value: before })
        out.push({ type: "element", tagName: "span", properties: { className: ["scramble"], ariaHidden: "true", dataText: inside }, children: [{ type: "text", value: inside }] })
        if (after) out.push({ type: "text", value: after })
      } else {
        walk(ch)
        out.push(ch)
      }
    }
    node.children = out
  }
  return (tree: any) => walk(tree)
}

function urlTransform(url: string) {
  if (/^file:/i.test(url) || /^artifact:/i.test(url) || /^cite:/i.test(url) || /^[A-Za-z]:[\\/]/.test(url)) return url
  return defaultUrlTransform(url)
}

type Source = { n: string; title: string; url: string }
const SOURCES_HEAD = /^[ \t]*(?:#{1,4}[ \t]*)?(?:\*\*|__)?(?:Sources?|References|Citations)(?:\*\*|__)?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*(.*)$/im
// a URL may contain balanced brackets (Wikipedia: .../Server_(computing)); stray ")" "." after a link are ignored
const URL_RX = String.raw`https?:\/\/(?:[^()\s<>]|\([^()\s<>]*\))+`
const SOURCE_LINE = new RegExp(String.raw`^[ \t]*(?:[-*][ \t]*)?\[?(\d{1,2})\]?[.):]?[ \t]*(?:\[([^\]]+)\]\((${URL_RX})\)[)\].,;]*|(.*?)<?(${URL_RX})>?)[ \t]*$`)
const SOURCE_PAIR = new RegExp(String.raw`\[(\d{1,2})\][ \t]*(?:\[([^\]]+)\]\((${URL_RX})\)|<?(${URL_RX})>?)`, "g")

/** Split off a trailing "Sources:" list and turn inline [1] / [1, 2] markers into citation links. */
export function prepCitations(text: string): { body: string; sources: Source[] } {
  const m = SOURCES_HEAD.exec(text)
  if (!m || m.index === undefined) return { body: text, sources: [] }
  const before = text.slice(0, m.index)
  const lines = (m[1] ? [m[1]] : []).concat(text.slice(m.index + m[0].length).split("\n"))
  const sources: Source[] = []
  let consumed = 0
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) { consumed++; if (sources.length) continue; else continue }
    const pairs = [...line.matchAll(SOURCE_PAIR)]
    if (pairs.length > 1) {                       // "[1] url [2] url" on one line
      for (const p of pairs) sources.push({ n: p[1], title: (p[2] || "").trim(), url: p[3] || p[4] })
      consumed++; continue
    }
    const s = SOURCE_LINE.exec(line)
    if (!s) break
    sources.push({ n: s[1], title: (s[2] || s[4] || "").replace(/[\s—–:-]+$/, "").trim(), url: s[3] || s[5].replace(/[.,;]+$/, "") })
    consumed++
  }
  if (!sources.length) return { body: text, sources: [] }
  const rest = lines.slice(consumed).join("\n").trim()
  const byN = new Map(sources.map((s) => [s.n, s.url]))
  const body = (before + (rest ? "\n\n" + rest : "")).replace(/\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\](?!\()/g, (all, nums: string) => {
    const ns = nums.split(/\s*[,;]\s*/)
    if (!ns.every((n) => byN.has(n))) return all
    return ns.map((n) => `[${n}](cite:${encodeURIComponent(byN.get(n)!)})`).join("")
  })
  return { body: body.trimEnd(), sources }
}

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, "") } catch { return u } }

function SourcesList({ sources }: { sources: Source[] }) {
  return (
    <div className="mt-4 max-w-full border-t border-border/50 pt-1.5 text-meta leading-[1.6] text-faint">
      <ol>
        {sources.map((s) => (
          <li key={s.n + s.url} className="flex min-w-0 items-baseline gap-1.5">
            <sup className="w-3 shrink-0 text-right text-meta tabular-nums">{s.n}</sup>
            <a href={s.url} target="_blank" rel="noreferrer" title={s.url}
              className="min-w-0 truncate no-underline hover:text-foreground hover:underline">{s.title || host(s.url)}</a>
            {s.title && <span className="shrink-0 text-faint">· {host(s.url)}</span>}
          </li>
        ))}
      </ol>
    </div>
  )
}

export const Markdown = memo(function Markdown({ text, className }: { text: string; className?: string }) {
  const { body, sources } = prepCitations(text)
  return (
    <div className={cn("prose-chat break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false, output: "htmlAndMathml" }], rehypeScramble]}
        urlTransform={urlTransform}
        components={{
          a: ({ href, children }) => {
            if (href && /^cite:/i.test(href)) {        // inline citation [1] → small numbered link to the source
              const url = decodeURIComponent(href.slice(5))
              return <sup className="cite ml-px leading-none"><a href={url} target="_blank" rel="noreferrer" title={host(url)}
                className="text-[0.68em] tabular-nums text-faint no-underline hover:text-foreground">{children}</a></sup>
            }
            const art = href ? /^artifact:(?:\/\/)?([\w-]+)/i.exec(href) : null
            if (art) return (                         // [title](artifact://a3): open it in the Artifacts pane
              <button onClick={() => { useApp.getState().set({ artifactSel: art[1], artifactOpen: art[1] } as any); showPanel("artifacts") }}
                className="link-ink">{children}</button>
            )
            const p = href ? fromHref(href) : null
            if (p) return <FileLink path={p}>{children}</FileLink>
            return (
              <a href={href} target="_blank" rel="noreferrer" className="link-ink">
                {children}
              </a>
            )
          },
          pre: ({ children }) => <>{children}</>,
          code: ({ className: cls, children }) => {
            const text = String(children ?? "")
            const m = /language-([\w+#-]+)/.exec(cls || "")
            if (m || text.includes("\n")) return <CodeBlock code={text.replace(/\n$/, "")} lang={m?.[1] || ""} />
            if (isWinPath(text)) return <FileLink path={text.trim()}><code className="mono text-[0.88em]">{text}</code></FileLink>
            return <code className="mono rounded bg-muted px-1.5 py-0.5 text-[0.86em] text-foreground/90">{text}</code>
          },
        }}
      >
        {linkifyPaths(prepMath(body))}
      </ReactMarkdown>
      {sources.length > 0 && <SourcesList sources={sources} />}
    </div>
  )
})
