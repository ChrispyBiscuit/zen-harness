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
import { AnswerBlock, BLOCK_LANGS, InlineImage } from "./blocks/AnswerBlock"
import { LiveContext } from "./blocks/Frame"
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
const BLOCK_NAMES = "chart|map|facts|timeline|cards|mermaid"
/** Answer blocks as small models actually write them: the block's name on the line after a bare ``` fence, or the
 *  opening fence at the end of a sentence. Both become a proper ```name fence on its own line. */
export function prepBlocks(text: string) {
  return text
    .replace(new RegExp(String.raw`^([ \t]*)\`\`\`[ \t]*\n[ \t]*(${BLOCK_NAMES})[ \t]*\n(?=[ \t]*[[{]|[ \t]*(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|mindmap)\b)`, "gim"),
      (_m, ind: string, name: string) => `${ind}\`\`\`${name.toLowerCase()}\n`)
    .replace(new RegExp(String.raw`([^\s\`])[ \t]*\`\`\`(${BLOCK_NAMES})[ \t]*\n`, "g"), (_m, ch: string, name: string) => `${ch}\n\n\`\`\`${name}\n`)
}

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
      .replace(/(^|[^\\$])\$(?=\d[\d,]*(?:\.\d+)?(?:[kKmMbB]n?)?(?:[\s).,;:!?%/*_\]\-–—+]|$))/g, "$1\\$")
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

const NUMERIC = /^[\s(+\-\u2212~≈<>]*[$€£¥₹]?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|[kKmMbBtT]n?|x|×)?[)\s]*$|^[–—-]$|^$/

/** rehype plugin: a table column whose cells are all numbers (or blank) is right-aligned, for scanning. */
function rehypeNumericColumns() {
  const text = (n: any): string => n.type === "text" ? n.value : (n.children || []).map(text).join("")
  const cells = (row: any) => (row.children || []).filter((c: any) => c.tagName === "td" || c.tagName === "th")
  const walk = (node: any) => {
    if (node.tagName === "table") {
      const rows: any[] = []
      const collect = (n: any) => { for (const c of n.children || []) { if (c.tagName === "tr") rows.push(c); else collect(c) } }
      collect(node)
      const body = rows.filter((r) => cells(r).some((c: any) => c.tagName === "td"))
      if (!body.length) return
      const cols = Math.max(...rows.map((r) => cells(r).length))
      for (let i = 0; i < cols; i++) {
        const vals = body.map((r) => text(cells(r)[i] || { type: "text", value: "" }).trim())
        if (!vals.some((v) => v && /\d/.test(v)) || !vals.every((v) => NUMERIC.test(v))) continue
        for (const r of rows) {
          const c = cells(r)[i]
          if (c) c.properties = { ...(c.properties || {}), className: [...((c.properties?.className as string[]) || []), "num"] }
        }
      }
      return
    }
    for (const c of node.children || []) walk(c)
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

const favicon = (u: string) => { try { return new URL("/favicon.ico", u).href } catch { return "" } }

/** The answer's sources as compact cards: number, site icon, title, site. */
function SourcesList({ sources }: { sources: Source[] }) {
  return (
    <div className="sources not-prose mt-4 max-w-full">
      <div className="mb-1.5 text-meta font-medium text-faint">Sources</div>
      <ol className="grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(210px,1fr))]">
        {sources.map((s) => (
          <li key={s.n + s.url} className="min-w-0">
            <a href={s.url} target="_blank" rel="noreferrer" title={s.url}
              className="group/src flex h-full min-w-0 items-start gap-2 rounded-[var(--radius-card)] bg-surface-1 px-2.5 py-1.5 no-underline ring-1 ring-line-1 transition-colors hover:bg-surface-2 hover:ring-line-2">
              <span className="mt-px w-3 shrink-0 text-right text-meta tabular-nums text-faint">{s.n}</span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 text-meta leading-snug text-muted-foreground group-hover/src:text-foreground">{s.title || host(s.url)}</span>
                <span className="mt-0.5 flex min-w-0 items-center gap-1 text-[10.5px] text-faint">
                  <img src={favicon(s.url)} alt="" referrerPolicy="no-referrer" loading="lazy" className="size-3 shrink-0 rounded-sm"
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none" }} />
                  <span className="truncate">{host(s.url)}</span>
                </span>
              </span>
            </a>
          </li>
        ))}
      </ol>
    </div>
  )
}

const REMARK: any[] = [remarkGfm, [remarkMath, { singleDollarTextMath: true }]]
const REHYPE: any[] = [[rehypeKatex, { throwOnError: false, strict: false, output: "htmlAndMathml" }], rehypeScramble, rehypeNumericColumns]

// Stable across renders: an inline object makes every element a new component type each render, so a streaming
// reply would remount its code blocks (and rebuild a chart or map) on every frame.
const COMPONENTS: any = {
          a: ({ href, children }: any) => {
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
          pre: ({ children }: any) => <>{children}</>,
          code: ({ className: cls, children }: any) => {
            const text = String(children ?? "")
            const m = /language-([\w+#-]+)/.exec(cls || "")
            const lang = m?.[1]?.toLowerCase() || ""
            if (BLOCK_LANGS.has(lang)) return <AnswerBlock lang={lang} code={text.replace(/\n$/, "")} />
            if (m || text.includes("\n")) return <CodeBlock code={text.replace(/\n$/, "")} lang={m?.[1] || ""} />
            if (isWinPath(text)) return <FileLink path={text.trim()}><code className="mono text-[0.88em]">{text}</code></FileLink>
            return <code className="mono rounded bg-muted px-1.5 py-0.5 text-[0.86em] text-foreground/90">{text}</code>
          },
          img: ({ src, alt }: any) => <InlineImage src={typeof src === "string" ? src : undefined} alt={alt} />,
}

/** Markdown for chat text. `live`: the text is still streaming (an unfinished chart or map shows a placeholder). */
export const Markdown = memo(function Markdown({ text, className, live = false }: { text: string; className?: string; live?: boolean }) {
  const { body, sources } = prepCitations(text)
  return (
    <div className={cn("prose-chat break-words", className)}>
      <LiveContext.Provider value={live}>
        <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} urlTransform={urlTransform} components={COMPONENTS}>
          {linkifyPaths(prepMath(prepBlocks(body)))}
        </ReactMarkdown>
      </LiveContext.Provider>
      {sources.length > 0 && !live && <SourcesList sources={sources} />}
    </div>
  )
})
