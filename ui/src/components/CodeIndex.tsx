import { useEffect, useState } from "react"
import { Database, Loader2, RotateCcw, Search } from "lucide-react"
import { MeterBar } from "@/components/MeterBar"
import { cn } from "@/lib/utils"

/** The code index behind focus_pack (backend/context_pack.py): function-level slices of a project, embedded once and
 *  reused. These pieces show what it is doing (status chip) and what the agent would see (the index view). */

type Status = { state: "idle" | "indexing" | "ready" | "error"; root?: string; files?: number; done?: number;
  total?: number; slices?: number; seconds?: number; started?: number; error?: string }
type Project = { root: string; indexed: boolean; files?: number; slices?: number; built?: number; seconds?: number;
  embedded?: boolean; saved_vectors?: number }
type Slice = { file: string; start: number; end: number; symbol: string | null; tokens: number }

const base = (p?: string) => (p || "").split(/[\\/]/).filter(Boolean).pop() || p || ""

/** Polls the index status: every 1.5 s while it builds, every 10 s otherwise. */
export function useCodeIndexStatus(): Status {
  const [st, setSt] = useState<Status>({ state: "idle" })
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      let next: Status = { state: "idle" }
      try { next = await (await fetch("/api/code_index/status")).json() } catch { /* app restarting */ }
      if (!alive) return
      setSt(next)
      timer = setTimeout(poll, next.state === "indexing" ? 1500 : 10000)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  return st
}

/** Status-bar chip, shown only while an index builds (or after it failed). */
export function CodeIndexChip() {
  const st = useCodeIndexStatus()
  if (st.state === "error") {
    return <span className="flex items-center gap-1.5 whitespace-nowrap text-warning" title={`Code index failed: ${st.error || ""}`}>
      <Database className="size-3.5" /> index failed</span>
  }
  if (st.state !== "indexing") return null
  const pct = st.total ? Math.round((100 * (st.done || 0)) / st.total) : undefined
  const elapsed = st.started ? Date.now() / 1000 - st.started : 0
  const eta = st.total && st.done && elapsed > 3 ? Math.max(0, Math.round((elapsed / st.done) * (st.total - st.done))) : null
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap"
      title={`Building the code index for ${st.root || "this project"} so the agent can pull the right functions from ` +
        `several files into its context at once (focus_pack). Only new or changed code is embedded; it runs in the ` +
        `background on the CPU.`}>
      <Loader2 className="size-3.5 animate-spin text-info" />
      <span className="text-foreground/80">Indexing {base(st.root)}</span>
      {pct != null && <MeterBar pct={pct} tone="info" label="Indexing" className="w-12" />}
      <span className="mono tabular-nums text-muted-foreground">
        {st.total ? `${st.done || 0}/${st.total} slices` : `${st.files || 0} files`}{eta != null ? ` · ~${eta < 90 ? `${eta}s` : `${Math.round(eta / 60)} min`}` : ""}
      </span>
    </span>
  )
}

function PackPreview({ root }: { root: string }) {
  const [q, setQ] = useState("")
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<{ text: string; used: number; slices: Slice[]; error?: string } | null>(null)
  const [showText, setShowText] = useState(false)
  const run = async () => {
    if (!q.trim()) return
    setBusy(true)
    try {
      const p = new URLSearchParams({ root, q, budget: "4000" })
      setRes(await (await fetch(`/api/code_index/pack?${p}`)).json())
    } catch (e: any) { setRes({ text: "", used: 0, slices: [], error: String(e) }) }
    setBusy(false)
  }
  return (
    <div className="mt-2 space-y-2">
      <form onSubmit={(e) => { e.preventDefault(); run() }} className="flex items-center gap-1.5">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="A goal or an error: what would the agent see?"
          className="h-7 min-w-0 flex-1 rounded-md border border-line-1 bg-surface-1 px-2 text-ui outline-none focus-visible:border-ring" />
        <button type="submit" disabled={busy || !q.trim()} title="Show the focus pack for this goal" aria-label="Show the focus pack"
          className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground disabled:opacity-40">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
        </button>
      </form>
      {res?.error && <div className="text-meta text-destructive">{res.error}</div>}
      {res && !res.error && (
        <div className="rounded-md border border-line-1 bg-surface-1">
          <div className="flex items-center justify-between border-b border-line-1 px-2 py-1 text-meta text-muted-foreground">
            <span>{res.slices.length} slices from {new Set(res.slices.map((s) => s.file)).size} files · ~{res.used.toLocaleString()} tokens</span>
            <button onClick={() => setShowText((v) => !v)} className="hover:text-foreground">{showText ? "list" : "full text"}</button>
          </div>
          {showText
            ? <pre className="mono max-h-80 overflow-auto whitespace-pre p-2 text-meta">{res.text}</pre>
            : <ul className="max-h-80 overflow-y-auto py-1">
              {res.slices.map((s, i) => (
                <li key={i} className="flex items-baseline gap-2 px-2 py-0.5 text-meta" title={`${s.file}:${s.start}-${s.end}`}>
                  <span className="mono min-w-0 flex-1 truncate text-foreground/85">{s.file}<span className="text-muted-foreground">:{s.start}-{s.end}</span></span>
                  {s.symbol && <span className="mono max-w-[45%] truncate text-muted-foreground">{s.symbol}</span>}
                  <span className="mono shrink-0 tabular-nums text-muted-foreground/70">{s.tokens}</span>
                </li>))}
            </ul>}
        </div>
      )}
    </div>
  )
}

/** The code-index section of the artifacts pane: each indexed project, a rebuild, and a "what would the agent see?"
 *  preview of the focus pack for a goal. Hidden until something has been indexed. */
export function CodeIndexSection() {
  const st = useCodeIndexStatus()
  const [projects, setProjects] = useState<Project[]>([])
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    fetch("/api/code_index/projects").then((r) => r.json()).then((d) => setProjects(d.projects || [])).catch(() => {})
  }, [st.state, st.root, st.slices])
  if (!projects.length && st.state !== "indexing") return null
  const rebuild = async (root: string) => {
    await fetch("/api/code_index/rebuild", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root }) }).catch(() => {})
  }
  return (
    <div className="border-b border-border pb-1">
      <div className="label-tech flex items-center gap-1.5 px-3 pb-1 pt-2 text-muted-foreground/60">
        <Database className="size-3" /> Code index
      </div>
      {projects.map((p) => {
        const building = st.state === "indexing" && st.root === p.root
        return (
          <div key={p.root} className={cn("px-3 py-1.5", open === p.root && "bg-muted/40")}>
            <div className="flex items-center gap-2">
              <button onClick={() => setOpen(open === p.root ? null : p.root)} title={p.root}
                className="min-w-0 flex-1 text-left">
                <span className="block truncate text-ui text-foreground/90">{base(p.root)}</span>
                <span className="block text-meta text-muted-foreground">
                  {building ? "indexing…" : `${p.files ?? 0} files · ${p.slices ?? 0} slices${p.embedded ? "" : " · keywords only"}` +
                    (p.built ? ` · built ${new Date(p.built * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "")}
                </span>
              </button>
              <button onClick={() => rebuild(p.root)} disabled={building} title="Rebuild (re-reads the files; unchanged code keeps its saved embeddings)"
                aria-label="Rebuild the code index" className="grid size-6 place-items-center rounded text-muted-foreground/70 hover:bg-surface-2 hover:text-foreground disabled:opacity-40">
                <RotateCcw className="size-3.5" />
              </button>
            </div>
            {open === p.root && <PackPreview root={p.root} />}
          </div>
        )
      })}
      {st.state === "indexing" && !projects.some((p) => p.root === st.root) && (
        <div className="px-3 py-1.5 text-meta text-muted-foreground">Indexing {base(st.root)}… {st.done || 0}/{st.total || "?"} slices</div>
      )}
    </div>
  )
}
