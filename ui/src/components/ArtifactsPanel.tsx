import { useEffect, useMemo, useState } from "react"
import { BarChart3, Code2, Download, Eye, ExternalLink, FileCode2, FileText, FolderOpen, GitBranch, Image as ImageIcon, Layers, Loader2, Table2 } from "lucide-react"
import { Markdown } from "./Markdown"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { CodeBlock, CopyButton } from "./CodeBlock"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import type { Msg } from "@/lib/types"
import { CodeIndexSection } from "./CodeIndex"

export type ArtifactRef = { id: string; title: string; atype: string; language?: string; version: number; action?: string }
type Meta = { id: string; title: string; type: string; language?: string; versions: { v: number; t: number; chars: number; note?: string }[] }

/** Artifacts touched in a conversation, newest version of each, in creation order. */
export function artifactsIn(messages: Msg[]): ArtifactRef[] {
  const by = new Map<string, ArtifactRef>()
  for (const m of messages) {
    const d = m.tool?.display as any
    if (m.kind === "tool" && d?.type === "artifact" && m.tool?.ok !== false && m.tool?.result != null) {
      const prev = by.get(d.id)
      by.set(d.id, { id: d.id, title: d.title, atype: d.atype, language: d.language, version: Math.max(prev?.version || 0, d.version) })
    }
  }
  return [...by.values()]
}

export type FileRef = { path: string; created: boolean; added: number; removed: number; edits: number }

/** Files the agent created or edited in a conversation (write/edit tools), newest change last. */
export function filesIn(messages: Msg[]): FileRef[] {
  const by = new Map<string, FileRef>()
  for (const m of messages) {
    const d = m.tool?.display as any
    if (m.kind === "tool" && m.tool?.name === "trash_path" && m.tool?.ok !== false && m.tool?.result != null) {
      const gone = (/^moved (.+?) to the agent trash/.exec(String(m.tool.result || "")) || [])[1] || ""   // result text names it
      for (const k of [...by.keys()]) if (k === gone || k.startsWith(gone + "\\") || k.startsWith(gone + "/")) by.delete(k)
      continue
    }
    if (m.kind === "tool" && d?.type === "diff" && d.path && !d.pending && m.tool?.ok !== false && m.tool?.result != null
        && !m.tool?.reverted) {
      const prev = by.get(d.path)
      by.delete(d.path)                                   // re-insert: the most recent change sorts last
      by.set(d.path, { path: d.path, created: prev ? prev.created : !!d.created,
        added: (prev?.added || 0) + (d.added || 0), removed: (prev?.removed || 0) + (d.removed || 0), edits: (prev?.edits || 0) + 1 })
    }
  }
  return [...by.values()]
}

const base = (p: string) => p.split(/[\\/]/).pop() || p
const dir = (p: string) => p.slice(0, Math.max(0, p.length - base(p).length - 1))

export const TYPE_ICON: Record<string, React.ReactNode> = {
  markdown: <FileText />, html: <Layers />, svg: <ImageIcon />, mermaid: <GitBranch />, chart: <BarChart3 />, code: <FileCode2 />,
  csv: <Table2 />, json: <Code2 />, text: <FileText />,
}
const PREVIEWABLE = new Set(["markdown", "html", "svg", "mermaid", "csv", "chart"])
const EXT: Record<string, string> = { markdown: "md", html: "html", svg: "svg", mermaid: "mmd", chart: "vl.json", csv: "csv", json: "json", text: "txt" }

function chartDoc(spec: string) {
  const safe = spec.replace(/</g, "\\u003c")
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#0f1012;color:#ddd;font-family:system-ui}
  #v{padding:14px}.err{color:#f88;white-space:pre-wrap;font-family:monospace;padding:14px}</style>
  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script><script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script></head><body><div id="v"></div><script>
  try { const spec = ${safe}; spec.width = spec.width || "container"; spec.autosize = spec.autosize || {type: "fit", contains: "padding"};
    vegaEmbed("#v", spec, {theme: "dark", actions: {export: true, source: false, compiled: false, editor: false}})
      .catch(e => document.body.innerHTML = '<div class="err">Chart error: ' + String(e.message || e).replace(/</g, "&lt;") + '</div>') }
  catch (e) { document.body.innerHTML = '<div class="err">Chart error: ' + String(e.message || e).replace(/</g, "&lt;") + '</div>' }
  </script></body></html>`
}

function mermaidDoc(src: string) {
  const esc = src.replace(/&/g, "&amp;").replace(/</g, "&lt;")
  return `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;padding:16px;background:#0f1012;color:#ddd;font-family:system-ui}
  .err{color:#f88;white-space:pre-wrap;font-family:monospace}</style></head><body><pre class="mermaid">${esc}</pre>
  <script type="module">import m from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  m.initialize({startOnLoad:false,theme:"dark",securityLevel:"strict"});
  try{await m.run()}catch(e){document.body.innerHTML='<div class="err">Diagram error: '+String(e.message||e).replace(/</g,"&lt;")+'</div>'}</script></body></html>`
}

function svgDoc(src: string) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#fff}
  body{display:grid;place-items:center}svg{max-width:100%;max-height:100%}</style></head><body>${src}</body></html>`
}

function CsvTable({ text }: { text: string }) {
  const rows = useMemo(() => {
    const out: string[][] = []
    for (const line of text.split(/\r?\n/).slice(0, 2000)) {
      if (!line.trim()) continue
      const cells: string[] = []
      let cur = "", q = false
      for (let i = 0; i < line.length; i++) {
        const ch = line[i]
        if (q) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++ } else if (ch === '"') q = false; else cur += ch }
        else if (ch === '"') q = true
        else if (ch === ",") { cells.push(cur); cur = "" }
        else cur += ch
      }
      cells.push(cur)
      out.push(cells)
    }
    return out
  }, [text])
  if (!rows.length) return <div className="p-4 text-muted-foreground">Empty table.</div>
  return (
    <div className="overflow-auto p-3">
      <table className="w-full border-collapse text-caption">
        <thead><tr>{rows[0].map((c, i) => <th key={i} className="sticky top-0 border-b border-border bg-surface-1 px-2 py-1.5 text-left font-medium">{c}</th>)}</tr></thead>
        <tbody>{rows.slice(1).map((r, i) => <tr key={i} className="odd:bg-muted/20">{r.map((c, j) => <td key={j} className="border-b border-border/50 px-2 py-1">{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

/** One artifact: preview / source, versions, copy, download. */
function ArtifactViewer({ convId, art }: { convId: string; art: ArtifactRef }) {
  const [ver, setVer] = useState<number | null>(null)          // null = follow the latest version
  const [mode, setMode] = useState<"preview" | "source">("preview")
  const [data, setData] = useState<{ meta: Meta; content: string; version: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const want = ver ?? art.version

  useEffect(() => { setVer(null) }, [art.id])
  useEffect(() => {
    setLoading(true)
    const p = new URLSearchParams({ conv: convId, id: art.id, ...(want ? { v: String(want) } : {}) })
    fetch(`/api/artifact?${p}`).then((r) => r.json()).then((d) => { if (!d.error) setData(d) }).finally(() => setLoading(false))
  }, [convId, art.id, want])

  const meta = data?.meta
  const type = meta?.type || art.atype
  const content = data?.content ?? ""
  const canPreview = PREVIEWABLE.has(type)
  const showPreview = canPreview && mode === "preview"
  const download = () => {
    const ext = type === "code" ? (meta?.language || "txt") : EXT[type] || "txt"
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain" }))
    const a = document.createElement("a")
    a.href = url
    a.download = `${(meta?.title || art.title).replace(/[^\w.-]+/g, "_")}.${ext}`
    a.click()
    URL.revokeObjectURL(url)
  }
  const btn = "flex items-center gap-1 rounded-md px-2 py-1 text-caption text-muted-foreground hover:bg-surface-2 hover:text-foreground"

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border py-2 pl-3 pr-10">
        <span className="text-muted-foreground [&_svg]:size-4">{TYPE_ICON[type] || <FileText />}</span>
        <div className="min-w-0 flex-1">
          <DialogTitle className="truncate text-ui font-medium">{meta?.title || art.title}</DialogTitle>
          <div className="text-meta text-muted-foreground">{type}{meta?.language ? ` · ${meta.language}` : ""} · {content.length.toLocaleString()} chars</div>
        </div>
        {loading && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
        {meta && meta.versions.length > 1 && (
          <select value={want ?? ""} onChange={(e) => setVer(Number(e.target.value) === art.version ? null : Number(e.target.value))}
            className="rounded-md border border-input bg-transparent px-1.5 py-0.5 text-caption" title="Version">
            {[...meta.versions].reverse().map((v) => <option key={v.v} value={v.v}>v{v.v}{v.v === art.version ? " (latest)" : ""}</option>)}
          </select>
        )}
        {canPreview && (
          <button className={btn} onClick={() => setMode(mode === "preview" ? "source" : "preview")} title="Preview / source">
            {mode === "preview" ? <><Code2 className="size-3.5" />Source</> : <><Eye className="size-3.5" />Preview</>}
          </button>
        )}
        <CopyButton text={content} />
        <button className={btn} onClick={download} title="Download"><Download className="size-3.5" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {!data ? null : showPreview && type === "markdown" ? (
          <div className="mx-auto max-w-4xl px-6 py-5"><Markdown text={content} /></div>
        ) : showPreview && (type === "html" || type === "svg" || type === "mermaid" || type === "chart") ? (
          <iframe key={`${art.id}:${data.version}`} title={meta?.title} sandbox="allow-scripts allow-popups allow-forms"
            srcDoc={type === "html" ? content : type === "svg" ? svgDoc(content) : type === "chart" ? chartDoc(content) : mermaidDoc(content)}
            className="h-full w-full border-0 bg-white" />
        ) : showPreview && type === "csv" ? (
          <CsvTable text={content} />
        ) : (
          <div className="p-2"><CodeBlock code={content} lang={type === "code" ? meta?.language || "" : type === "markdown" ? "markdown" : type === "chart" ? "json" : type} /></div>
        )}
      </div>
    </div>
  )
}

/** The Artifacts pane: a list (newest first); clicking one opens it in a preview window. */
export function ArtifactsPanel() {
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const sel = useApp((s) => (s as any).artifactSel as string | undefined)       // highlighted in the list
  const openId = useApp((s) => (s as any).artifactOpen as string | undefined)   // shown in the preview window
  const set = useApp((s) => s.set)
  const list = useMemo(() => (conv ? artifactsIn(conv.messages) : []), [conv?.messages])
  const files = useMemo(() => (conv ? filesIn(conv.messages) : []), [conv?.messages])
  const openFile = useApp((s) => s.openFile)
  const open = openId ? list.find((a) => a.id === openId) : undefined

  if (!conv || (!list.length && !files.length)) {
    return (
      <div className="flex h-full flex-col">
      <CodeIndexSection />
      <div className="px-6 py-12 text-center text-ui text-muted-foreground">
        <Layers className="mx-auto mb-3 size-6 opacity-60" />
        Documents, pages, diagrams and code the agent creates show up here, with every file it writes or edits, and update
        as it revises them.
      </div>
      </div>
    )
  }
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-3 py-2 text-caption text-muted-foreground">
        {[list.length ? `${list.length} artifact${list.length === 1 ? "" : "s"}` : "", files.length ? `${files.length} file${files.length === 1 ? "" : "s"}` : ""]
          .filter(Boolean).join(" · ")} in this chat · click to open
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        <CodeIndexSection />
        {list.length > 0 && files.length > 0 && <div className="label-tech px-3 pb-1 pt-2 text-faint">Artifacts</div>}
        {[...list].reverse().map((a) => (
          <button key={a.id} onClick={() => set({ artifactSel: a.id, artifactOpen: a.id } as any)} title={a.title}
            className={cn("flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-surface-2 [&_svg]:size-4",
              a.id === sel && "bg-muted/60")}>
            <span className="shrink-0 text-muted-foreground">{TYPE_ICON[a.atype] || <FileText />}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-ui text-foreground/90">{a.title}</span>
              <span className="block text-meta text-muted-foreground">{a.atype}{a.language ? ` · ${a.language}` : ""} · v{a.version}</span>
            </span>
            <Eye className="shrink-0 text-faint" />
          </button>
        ))}
        {files.length > 0 && <div className="label-tech px-3 pb-1 pt-3 text-faint">Files</div>}
        {[...files].reverse().map((f) => (
          <div key={f.path} className="group flex items-center gap-2.5 px-3 py-1.5 hover:bg-surface-2 [&_svg]:size-4">
            <button onClick={() => openFile(f.path)} title={f.path} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
              <span className="shrink-0 text-muted-foreground"><FileCode2 /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-ui text-foreground/90">{base(f.path)}</span>
                <span className="block truncate text-meta text-muted-foreground">
                  {f.created ? "created" : "edited"}{f.edits > 1 ? ` · ${f.edits} changes` : ""} · <span className="text-success">+{f.added}</span> <span className="text-destructive">−{f.removed}</span> · {dir(f.path)}
                </span>
              </span>
            </button>
            <button onClick={() => api.open(f.path, "default")} title="Open in its default app" aria-label="Open in its default app"
              className="shrink-0 text-faint opacity-0 hover:text-foreground group-hover:opacity-100"><ExternalLink /></button>
            <button onClick={() => api.open(f.path, "reveal")} title="Show in Explorer" aria-label="Show in Explorer"
              className="shrink-0 text-faint opacity-0 hover:text-foreground group-hover:opacity-100"><FolderOpen /></button>
          </div>
        ))}
      </div>
      <Dialog open={!!open} onOpenChange={(v) => { if (!v) set({ artifactOpen: undefined } as any) }}>
        <DialogContent className="flex h-[86vh] w-[92vw] max-w-[92vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-6xl">
          {open && <ArtifactViewer convId={conv.id} art={open} />}
        </DialogContent>
      </Dialog>
    </div>
  )
}
