import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle, Bot, GraduationCap, Check, ChevronRight, CircleDashed, CircleDot, CheckCircle2, Cpu, ExternalLink,
  FileDiff, FileText, Globe, Image as ImageIcon, ListChecks, Loader2, MinusCircle, Pin, Search, Terminal,
  Undo2, Workflow, X, FolderTree, Wrench, Calculator, Code2, ShieldCheck, Table2,
} from "lucide-react"
import katex from "katex"
import { TYPE_ICON } from "@/components/ArtifactsPanel"
import { showPanel } from "@/lib/dock"
import { Button } from "@/components/ui/button"
import { Markdown } from "@/components/Markdown"
import { CodeBlock, CopyButton } from "@/components/CodeBlock"
import { api, rawUrl } from "@/lib/api"
import { basename, dirname } from "@/lib/paths"
import { useApp } from "@/lib/store"
import type { Display, Msg, Tool } from "@/lib/types"
import { cn } from "@/lib/utils"
import type { Tone } from "@/lib/tone"
import { toast } from "sonner"
import { ChecklistView, ProgressBar, current as currentItem, progress } from "@/components/Checklist"

/* ------------------------------------------------------------------ shared pieces */

/* ------------------------------------------------------------------ tool timing: "running · 45 s" */

/** Per tool card: is it live (no result yet, in a running chat), seconds so far, and the final duration once done. */
type ToolTime = { live: boolean; elapsed: number | null; done: number | null }
const ToolTimeCtx = createContext<ToolTime>({ live: false, elapsed: null, done: null })
// when a card was first seen without a result / with one (client-side clock; the backend's t0/t1 win when present)
const firstSeen = new Map<string, number>(), endSeen = new Map<string, number>()

const fmtSecs = (s: number) => (s < 60 ? `${Math.floor(s)} s` : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`)

/** The timing for one tool card: ticks every second while the tool runs. */
function useToolTime(msg: Msg, convId: string, index: number): ToolTime {
  const tool = msg.tool!
  const convRunning = useApp((st) => !!st.threads.find((t) => t.id === convId)?.running)
  const key = `${convId}:${msg.id || index}`
  const pending = tool.result == null && !tool.awaiting
  const now = Date.now()
  if (pending && !firstSeen.has(key)) firstSeen.set(key, now)
  if (!pending && firstSeen.has(key) && !endSeen.has(key)) endSeen.set(key, now)
  const t0 = (tool as any).t0 ? (tool as any).t0 * 1000 : firstSeen.get(key)
  const t1 = (tool as any).t1 ? (tool as any).t1 * 1000 : endSeen.get(key)
  const live = pending && convRunning
  const [, tick] = useState(0)
  useEffect(() => {
    if (!live) return
    const id = window.setInterval(() => tick((x) => x + 1), 1000)
    return () => window.clearInterval(id)
  }, [live])
  return {
    live,
    elapsed: live && t0 ? Math.max(0, (Date.now() - t0) / 1000) : null,
    done: !pending && t0 && t1 && t1 >= t0 ? (t1 - t0) / 1000 : null,
  }
}

/** " · 45 s" while the card's tool runs (inside a status pill). */
function Elapsed() {
  const t = useContext(ToolTimeCtx)
  return t.live && t.elapsed != null ? <span className="tabular-nums opacity-80"> · {fmtSecs(t.elapsed)}</span> : null
}

/** The finished duration, small and quiet (only when it took a noticeable while). */
function Took({ className }: { className?: string }) {
  const t = useContext(ToolTimeCtx)
  return !t.live && t.done != null && t.done >= 1
    ? <span className={cn("mono shrink-0 text-meta tabular-nums text-faint", className)} title="How long this step took">{fmtSecs(t.done)}</span>
    : null
}

/** The last line of a running tool's streamed output, under its (folded) card: a sign of life. */
function LastLine({ text }: { text: string }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5 px-2 pt-1 text-meta text-muted-foreground">
      <span className="relative flex size-1.5 shrink-0"><span className="absolute inline-flex size-full animate-ping rounded-full bg-info/50" />
        <span className="relative inline-flex size-1.5 rounded-full bg-info/80" /></span>
      <span className="mono min-w-0 truncate">{text}</span>
    </div>
  )
}

export function StatusPill({ tone, children, spin }: { tone: Tone; children: React.ReactNode; spin?: boolean }) {
  // a step that fails while you watch glitches once as it turns red (index.css .glitch-once); old failures don't
  const prev = useRef(tone)
  const [glitch, setGlitch] = useState(false)
  useEffect(() => {
    if (tone === "destructive" && prev.current !== "destructive") setGlitch(true)
    prev.current = tone
  }, [tone])
  return (
    <span data-tone={tone} onAnimationEnd={() => setGlitch(false)}
      className={cn("tone-text tone-soft inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-meta font-medium", glitch && "glitch-once")}>
      {spin && <Loader2 className="size-3 animate-spin" />}
      {children}
    </span>
  )
}

function CardShell({ icon, title, right, children, className, tone, fold, after }: {
  icon: React.ReactNode; title: React.ReactNode; right?: React.ReactNode; children?: React.ReactNode; className?: string
  /** a state edge (e.g. warning while it waits for approval); no tone = the neutral surface border */
  tone?: Tone
  /** foldable body: starts collapsed (header only); `force` keeps it open (e.g. waiting for approval) */
  fold?: { k: string; force?: boolean }
  /** always shown below the body, folded or not (images, files, the approval bar) */
  after?: React.ReactNode
}) {
  const opened = useApp((s) => (fold ? !!s.expanded[fold.k] : true))
  const toggle = useApp((s) => s.toggle)
  const open = !fold || fold.force || opened
  const foldable = fold && !fold.force
  return (
    <div data-tone={tone} className={cn("overflow-hidden rounded-card border bg-surface-1",
      tone ? "tone-edge" : "border-line-1", className)}>
      <div className={cn("flex min-h-9 items-center gap-2 px-(--card-px) py-(--card-py)",
        foldable && "transition-ui cursor-pointer select-none hover:bg-surface-2 active:bg-surface-3")}
        role={foldable ? "button" : undefined} tabIndex={foldable ? 0 : undefined} aria-expanded={foldable ? open : undefined}
        onKeyDown={foldable ? (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); toggle(fold.k) } } : undefined}
        onClick={foldable ? (e) => { if (!(e.target as HTMLElement).closest("button, a")) toggle(fold.k) } : undefined}>
        <span className={cn("[&_svg]:size-4", tone ? "tone-text" : "text-muted-foreground")}>{icon}</span>
        <div className="min-w-0 flex-1 truncate text-ui">{title}</div>
        {right}
        {fold && !fold.force && <ChevronRight className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />}
      </div>
      {open && children}
      {after}
    </div>
  )
}

function PathLink({ path, label }: { path: string; label?: string }) {
  const openFile = useApp((s) => s.openFile)
  return (
    <button onClick={() => openFile(path)} title={path}
      className="mono truncate text-caption text-foreground/90 hover:underline">
      {label ?? basename(path)}
    </button>
  )
}

export function ApprovalBar({ tool }: { tool: Tool }) {
  const ap = tool.approval
  const [busy, setBusy] = useState(false)
  if (!tool.awaiting || !ap) return null
  const pattern = (ap.rule_label || "").split(": ").slice(1).join(": ")
  const go = async (d: string) => {
    setBusy(true)
    try {
      await api.approve(ap.call_id, d)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div data-tone="warning" className="tone-edge tone-wash border-t px-3 py-2.5">
      <div className="tone-text mb-2 flex items-center gap-1.5 text-caption font-medium">
        <AlertTriangle className="size-3.5" /> Waiting for your approval
      </div>
      {ap.warnings?.map((w) => (
        <div key={w} className="tone-text mb-2 text-caption opacity-80">{w}</div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" disabled={busy} onClick={() => go("once")}>Allow once</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => go("session")}>Allow for this chat</Button>
        {pattern && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => go("always")} title="Saved to your allow rules">
            Always allow <code className="mono ml-1 max-w-56 truncate text-meta text-muted-foreground">{pattern}</code>
          </Button>
        )}
        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" disabled={busy} onClick={() => go("deny")}>
          Deny
        </Button>
      </div>
    </div>
  )
}

function useStick(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight
  }, [dep])
  return ref
}

/* ------------------------------------------------------------------ terminal */

/** A web search / multi-page read: one summary line, collapsed by default; click to see the pages and results. */
function WebHitsCard({ d, k }: { d: Display; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const pages: any[] = (d as any).pages || [], links: any[] = (d as any).links || []
  const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, "") } catch { return u } }
  const item = (h: any, read: boolean) => (
    <a key={h.url} href={h.url} target="_blank" rel="noreferrer" className="block px-(--card-px) py-1.5 hover:bg-surface-2">
      <div className={cn("truncate text-caption", read ? "text-foreground/90" : "text-muted-foreground")}>{h.title || h.url}</div>
      <div className="truncate text-caption text-faint">{host(h.url)}</div>
    </a>)
  const queries: string[] = (d as any).queries || []
  const verb = queries.length ? "Searched the web" : "Read web pages"
  const count = pages.length ? `read ${pages.length} page${pages.length === 1 ? "" : "s"}` : `${(d as any).results ?? links.length} results`
  const secs = (d as any).seconds != null ? ` · ${Math.round((d as any).seconds)}s` : ""
  return (
    <div className="overflow-hidden rounded-card border border-line-1 bg-surface-1">
      <button onClick={() => toggle(k)} className="flex min-h-9 w-full items-center gap-2 px-(--card-px) py-(--card-py) text-left transition-ui hover:bg-surface-2 active:bg-surface-3">
        <span className="text-muted-foreground [&_svg]:size-4"><Search /></span>
        <span className="min-w-0 flex-1 truncate text-ui">{verb}{" "}
          <span className="font-normal text-muted-foreground">{queries.map((q) => `“${q}”`).join(" · ")}</span></span>
        <span className="shrink-0 text-caption text-muted-foreground">{count}{secs}</span>
        {(pages.length > 0 || links.length > 0) &&
          <ChevronRight className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />}
      </button>
      {open && (
        <div className="divide-y divide-line-1 border-t border-line-1">
          {pages.map((h) => item(h, true))}
          {links.length > 0 && pages.length > 0 && <div className="px-(--card-px) pt-1.5 text-meta uppercase tracking-wide text-faint">also found</div>}
          {links.slice(0, pages.length ? 4 : 10).map((h) => item(h, false))}
        </div>
      )}
    </div>
  )
}

function TerminalCard({ tool, d, k }: { tool: Tool; d: Display; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const out: string = (d.output || "").replace(/\n+$/, "")
  const lines = out ? out.split("\n") : []
  const LIMIT = 24
  const shown = expanded ? lines : lines.slice(-LIMIT)
  const ref = useStick(out.length)
  const running = tool.result == null && !tool.awaiting
  let status: React.ReactNode
  if (tool.awaiting) status = <StatusPill tone="warning">needs approval</StatusPill>
  else if ((tool as any).drafting) status = <StatusPill tone="info" spin>writing<Elapsed /></StatusPill>
  else if (running) status = <StatusPill tone="info" spin>running<Elapsed /></StatusPill>
  else if (d.exit == null && tool.ok === false) status = <StatusPill tone="destructive">not run</StatusPill>
  else if (d.stopped) status = <StatusPill tone="destructive">{d.stopped}</StatusPill>
  else status = <StatusPill tone={d.exit === 0 ? "success" : "destructive"}>exit {d.exit}{d.seconds != null ? ` · ${d.seconds}s` : ""}</StatusPill>
  const title = d.shell === "cmd" ? "Command Prompt" : d.shell === "pwsh" ? "PowerShell 7" : "PowerShell"
  const cmdLines = String(d.command || "").split("\n")
  return (
    <CardShell icon={<Terminal />} tone={tool.awaiting ? "warning" : undefined}
      fold={{ k: `${k}:body`, force: !!tool.awaiting }} after={<ApprovalBar tool={tool} />}
      title={<span className="flex min-w-0 items-center gap-2" title={d.cwd}><span className="shrink-0 font-medium">{title}</span>
        <span className="mono truncate text-caption text-muted-foreground">{cmdLines[0]}{cmdLines.length > 1 ? " …" : ""}</span></span>}
      right={<div className="flex items-center gap-1">{out && <CopyButton text={out} />}{status}</div>}>
      <div ref={ref} className="mono max-h-[420px] overflow-auto border-t border-line-1 bg-code px-(--card-px) py-2.5 text-caption leading-[1.55]">
        {cmdLines.map((c, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            <span className="select-none text-success/80">{i === 0 ? (d.shell === "cmd" ? ">" : "PS>") : ">>"}</span>{" "}
            <span className="text-foreground">{c}</span>
          </div>
        ))}
        {!expanded && lines.length > LIMIT && (
          <button onClick={() => toggle(k)} className="my-1 text-caption text-muted-foreground hover:text-foreground">
            Show {lines.length - LIMIT} earlier lines
          </button>
        )}
        {shown.length > 0 && <pre className="mt-1 whitespace-pre-wrap break-all text-foreground/75">{shown.join("\n")}</pre>}
        {!out && tool.result != null && d.exit != null && <div className="mt-1 text-muted-foreground">(no output)</div>}
        {expanded && lines.length > LIMIT && (
          <button onClick={() => toggle(k)} className="mt-1 text-caption text-muted-foreground hover:text-foreground">Collapse</button>
        )}
      </div>
    </CardShell>
  )
}

/* ------------------------------------------------------------------ diff */

type DiffRow = { kind: "add" | "del" | "ctx" | "hunk"; text: string; a?: number; b?: number }

function parseDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  let a = 0, b = 0
  for (const line of diff.split("\n")) {
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (h) {
      a = Number(h[1]); b = Number(h[2])
      rows.push({ kind: "hunk", text: line })
    } else if (line.startsWith("+")) rows.push({ kind: "add", text: line.slice(1), b: b++ })
    else if (line.startsWith("-")) rows.push({ kind: "del", text: line.slice(1), a: a++ })
    else if (line.startsWith("…")) rows.push({ kind: "hunk", text: line })
    else rows.push({ kind: "ctx", text: line.slice(1), a: a++, b: b++ })
  }
  return rows
}

function DiffCard({ tool, d, k, convId, index }: { tool: Tool; d: Display; k: string; convId: string; index: number }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const rows = useMemo(() => (d.diff ? parseDiff(d.diff) : []), [d.diff])
  const LIMIT = 40
  const shown = expanded ? rows : rows.slice(0, LIMIT)
  const pending = !!d.pending
  const verb = pending ? (d.created ? "New file" : "Proposed edit") : d.created ? "Created" : "Edited"
  let right: React.ReactNode
  if (pending) right = tool.awaiting ? <StatusPill tone="warning">needs approval</StatusPill>
    : tool.result == null ? <StatusPill tone="info" spin>applying<Elapsed /></StatusPill> : <StatusPill tone="destructive">not applied</StatusPill>
  else if (tool.reverted) right = <StatusPill tone="muted">reverted</StatusPill>
  else right = (
    <Button size="xs" variant="ghost" onClick={async () => {
      try {
        const r = await api.revert(convId, index)
        toast.success(r.message)
      } catch (e: any) {
        toast.error(e.message)
      }
    }}><Undo2 /> Revert</Button>
  )
  return (
    <CardShell icon={<FileDiff />} tone={tool.awaiting ? "warning" : undefined}
      fold={{ k: `${k}:body`, force: !!tool.awaiting }} after={<ApprovalBar tool={tool} />}
      title={<span className="flex min-w-0 items-center gap-2">
        <span className="font-medium">{verb}</span><PathLink path={d.path} />
        <span className="mono text-caption text-success">+{d.added ?? 0}</span>
        <span className="mono text-caption text-destructive">−{d.removed ?? 0}</span>
        <span className="mono hidden truncate text-meta text-muted-foreground lg:inline">{dirname(d.path || "")}</span>
      </span>}
      right={right}>
      {rows.length > 0 && (
        <div className="mono max-h-[520px] overflow-auto border-t border-line-1 bg-code py-1 text-caption leading-[1.55]">
          {shown.map((r, i) => (
            <div key={i} className={cn("flex min-w-max",
              r.kind === "add" && "bg-success/[0.10]", r.kind === "del" && "bg-destructive/[0.10]",
              r.kind === "hunk" && "text-faint")}>
              <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground/40">{r.kind === "hunk" ? "" : r.a ?? ""}</span>
              <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground/40">{r.kind === "hunk" ? "" : r.b ?? ""}</span>
              <span className={cn("w-4 shrink-0 select-none",
                r.kind === "add" ? "text-success" : r.kind === "del" ? "text-destructive" : "text-transparent")}>
                {r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}
              </span>
              <span className={cn("whitespace-pre pr-4", r.kind === "add" ? "text-success-foreground text-foreground" : r.kind === "del" ? "text-foreground/80" : "text-foreground/70",
                r.kind === "hunk" && "pl-0 text-caption")}>{r.text}</span>
            </div>
          ))}
          {rows.length > LIMIT && (
            <button onClick={() => toggle(k)} className="px-3 py-1 text-caption text-muted-foreground hover:text-foreground">
              {expanded ? "Collapse" : `Show all ${rows.length} lines`}
            </button>
          )}
        </div>
      )}
    </CardShell>
  )
}

/* ------------------------------------------------------------------ tracker */

export { StepIcon } from "@/components/Checklist"

function TrackerCard({ tool, d, k }: { tool: Tool; d: Display; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const { done, total, pct } = progress(d)
  const notes: string[] = d.notes || []
  return (
    <CardShell icon={<ListChecks />}
      title={<span className="flex items-center gap-2"><span className="font-medium">Checklist</span>
        <span className="text-caption text-muted-foreground">{done}/{total} done</span>
        {tool.result == null && <Loader2 className="size-3 animate-spin text-muted-foreground" />}</span>}
      right={d.path && <PathLink path={d.path} label="open file" />}>
      <div className="border-t border-line-1 px-(--card-px) pb-3 pt-2.5">
        {d.goal && <div className="mb-2 text-ui font-medium">{d.goal}</div>}
        <ProgressBar pct={pct} className="mb-3" />
        <ChecklistView t={d} />
        {notes.length > 0 && (
          <div className="mt-2">
            <button onClick={() => toggle(k)} className="text-caption text-muted-foreground hover:text-foreground">
              {expanded ? "Hide notes" : `Notes (${notes.length})`}
            </button>
            {expanded && (
              <ul className="mt-1 space-y-0.5 text-caption text-muted-foreground">
                {notes.map((n, i) => <li key={i}>• {n}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </CardShell>
  )
}

/* ------------------------------------------------------------------ small cards */

function Row({ icon, children, right, onClick, tone }: { icon: React.ReactNode; children: React.ReactNode; right?: React.ReactNode; onClick?: () => void; tone?: Tone }) {
  return (
    <div onClick={onClick} data-tone={tone} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" && e.target === e.currentTarget) onClick() } : undefined}
      className={cn("flex min-h-(--row-h) items-center gap-2 rounded-lg px-2 py-1 text-ui text-muted-foreground",
        onClick && "transition-ui cursor-pointer hover:bg-surface-2 active:bg-surface-3", tone && "tone-text")}>
      <span className="[&_svg]:size-3.5">{icon}</span>
      <div className="min-w-0 flex-1 truncate">{children}</div>
      <RowTime />
      {right}
    </div>
  )
}

/** A row's timing: the ticking seconds while it runs, the quiet duration after. */
function RowTime() {
  const t = useContext(ToolTimeCtx)
  if (t.live && t.elapsed != null) return <span className="mono shrink-0 text-meta tabular-nums text-info/80">{fmtSecs(t.elapsed)}</span>
  return <Took />
}

const ICONS: Record<string, React.ReactNode> = {
  web_search: <Search />, web_fetch: <Globe />, list_dir: <FolderTree />, find_files: <Search />, search_text: <Search />,
  read_file: <FileText />, read_document: <FileText />, make_dir: <FolderTree />, move_path: <FolderTree />,
  trash_path: <X />, screenshot: <ImageIcon />, wait: <Loader2 />, bg_output: <Cpu />, bg_list: <Cpu />, bg_stop: <Cpu />,
  wait_task: <Workflow />, task_status: <Workflow />,
}

function argSummary(tool: Tool) {
  const desc = tool.desc || ""
  const m = /^[\w_]+:\s*(\{.*\})$/s.exec(desc)
  if (m) {
    try {
      const a = JSON.parse(m[1])
      const v = a.query || a.url || a.path || a.pattern || a.id || a.command || Object.values(a)[0] || ""
      if (typeof v === "string" || typeof v === "number") return v
      // lists and objects: a readable summary instead of "[object Object]"
      if (Array.isArray(v)) return v.length && typeof v[0] !== "object" ? v.join(", ")
        : `${v.length} item${v.length === 1 ? "" : "s"}${v[0] && typeof v[0] === "object" ? ` (${Object.keys(v[0]).slice(0, 3).join(", ")})` : ""}`
      return Object.keys(v).slice(0, 4).join(", ")
    } catch { /* fallthrough */ }
  }
  return desc.split("\n")[0]
}

function GenericCard({ tool, k }: { tool: Tool; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const running = tool.result == null && !tool.awaiting
  const label = tool.name.replace(/^mcp__/, "").replace(/__/g, " › ").replace(/_/g, " ")
  return (
    <div data-tone={tool.awaiting ? "warning" : undefined} className={cn("rounded-card", tool.awaiting && "tone-edge border bg-surface-1")}>
      <Row icon={ICONS[tool.name] || (tool.kind === "mcp" ? <Bot /> : <Wrench />)} onClick={() => toggle(k)} tone={tool.ok === false ? "destructive" : undefined}
        right={running ? <Loader2 className="size-3.5 animate-spin" /> : tool.ok === false ? <X className="size-3.5" /> : <ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />}>
        <span className="text-foreground/85">{label}</span> <span className="mono text-caption">{String(argSummary(tool)).slice(0, 160)}</span>
      </Row>
      {expanded && tool.result != null && (
        <pre className="mono mx-2 mb-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-code px-3 py-2 text-caption text-foreground/70">{tool.result}</pre>
      )}
      <ApprovalBar tool={tool} />
    </div>
  )
}

function DraftCard({ tool, d }: { tool: Tool; d: Display }) {
  const ref = useStick(String(d.text || "").length)
  const live = (tool as any).drafting
  const label = String(d.name || tool.name || "tool").replace(/_/g, " ")
  return (
    <CardShell icon={<FileText />} title={<span className="flex items-center gap-2"><span className="font-medium">{label}</span>
      {d.target && <span className="mono truncate text-caption text-muted-foreground">{d.target}</span>}</span>}
      right={live ? <StatusPill tone="info" spin>writing · {Number(d.chars || 0).toLocaleString()} chars<Elapsed /></StatusPill>
        : tool.result == null ? <StatusPill tone="info" spin>running<Elapsed /></StatusPill>
        : <StatusPill tone={tool.ok === false ? "destructive" : "success"}>{tool.ok === false ? String(tool.result).slice(0, 60) : "done"}</StatusPill>}>
      {d.text && (
        <div ref={ref} className="mono max-h-72 overflow-auto whitespace-pre-wrap break-all border-t border-line-1 bg-code px-(--card-px) py-2.5 text-caption leading-[1.5] text-foreground/75">{d.text}</div>
      )}
    </CardShell>
  )
}

function PythonCard({ tool, d, k }: { tool: Tool; d: Display; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const openFile = useApp((s) => s.openFile)
  const running = tool.result == null && !tool.awaiting
  const images: string[] = d.images || []
  const files: string[] = d.files || []
  const out = String(d.output || "").replace(/^\((new (sandbox|Python) session[^)]*|loaded packages:[^)]*|copied into[^)]*)\)\n?/gm, "").replace(/\n+$/, "")
  const codeLines = String(d.code || "").replace(/\n+$/, "").split("\n")
  const CODE_LIMIT = 10
  const shownCode = expanded ? codeLines : codeLines.slice(0, CODE_LIMIT)
  const ref = useStick(out.length)
  const failed = tool.ok === false || d.error
  let status: React.ReactNode
  if (tool.awaiting) status = <StatusPill tone="warning">needs approval</StatusPill>
  else if ((tool as any).drafting) status = <StatusPill tone="info" spin>writing<Elapsed /></StatusPill>
  else if (running) status = <StatusPill tone="info" spin>running<Elapsed /></StatusPill>
  else status = <><Took /><StatusPill tone={failed ? "destructive" : "success"}>{failed ? "error" : "done"}</StatusPill></>
  return (
    <CardShell icon={d.sandbox ? <ShieldCheck /> : <Code2 />} tone={tool.awaiting ? "warning" : undefined}
      fold={{ k: `${k}:body`, force: !!tool.awaiting }}
      after={<>
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t border-line-1 p-2">
            {images.map((p) => <img key={p} src={rawUrl(p)} onClick={() => openFile(p)} className="max-h-80 max-w-full cursor-zoom-in rounded-lg border border-border bg-white object-contain" />)}
          </div>
        )}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-t border-line-1 px-2 py-1.5 text-caption">
            {files.slice(0, 12).map((p) => (
              <button key={p} onClick={() => openFile(p)} className="rounded-chip border border-line-1 px-2 py-0.5 text-foreground/90 hover:bg-surface-2"><FileText className="mr-1 inline size-3" />{basename(p)}</button>
            ))}
          </div>
        )}
        <ApprovalBar tool={tool} />
      </>}
      title={<span className="flex min-w-0 items-center gap-2"><span className="shrink-0 font-medium">Python</span>
        <span className="shrink-0 text-caption text-muted-foreground">{d.sandbox ? "sandbox" : "on this PC"}</span>
        <span className="mono truncate text-caption text-faint">{codeLines.find((l) => l.trim() && !l.trim().startsWith("#")) || ""}</span></span>}
      right={<div className="flex items-center gap-1">{out && <CopyButton text={out} />}{status}</div>}>
      <div ref={ref} className="mono max-h-[460px] overflow-auto border-t border-line-1 bg-code px-(--card-px) py-2.5 text-caption leading-[1.55]">
        {shownCode.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            <span className="select-none text-success/80">{i === 0 ? ">>>" : "..."}</span>{" "}
            <span className="text-foreground">{line}</span>
          </div>
        ))}
        {codeLines.length > CODE_LIMIT && (
          <button onClick={() => toggle(k)} className="my-1 text-caption text-muted-foreground hover:text-foreground">
            {expanded ? "Collapse code" : `… ${codeLines.length - CODE_LIMIT} more lines of code`}
          </button>
        )}
        {out && <div className={cn("mt-1.5 whitespace-pre-wrap break-all", failed ? "text-destructive/90" : "text-foreground/75")}>{out}</div>}
        {running && <span className="mt-1 inline-block h-3.5 w-2 animate-pulse bg-foreground/60 align-middle" />}
      </div>
    </CardShell>
  )
}

function TableCard({ tool, d, k }: { tool: Tool; d: Display; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const cols: string[] = d.columns || []
  const rows: string[][] = d.rows || []
  const shown = expanded ? rows : rows.slice(0, 8)
  return (
    <CardShell icon={<Table2 />}
      title={<span>Query <span className="mono text-caption font-normal text-muted-foreground">{String(d.sql || "").replace(/\s+/g, " ").slice(0, 90)}</span></span>}
      right={<span className="text-caption text-muted-foreground">{Number(d.total || 0).toLocaleString()} row{d.total === 1 ? "" : "s"}</span>}>
      <div className="max-h-80 overflow-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-caption">
          <thead><tr>{cols.map((c) => <th key={c} className="sticky top-0 border-b border-line-1 bg-surface-1 px-2 py-1 text-left font-medium">{c}</th>)}</tr></thead>
          <tbody>{shown.map((r, i) => <tr key={i} className="odd:bg-muted/20">{r.map((v, j) => <td key={j} className="mono border-b border-line-1 px-2 py-0.5">{v}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {rows.length > 8 && <button onClick={() => toggle(k)} className="mt-1 text-caption text-muted-foreground hover:text-foreground">{expanded ? "Show fewer" : `Show all ${rows.length} rows`}</button>}
    </CardShell>
  )
}

function MathCard({ tool, d, k }: { tool: Tool; d: Display; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const html = useMemo(() => {
    try { return katex.renderToString(String(d.latex || d.result || ""), { throwOnError: false, displayMode: false }) }
    catch { return "" }
  }, [d.latex, d.result])
  return (
    <div className="rounded-card">
      <Row icon={<Calculator />} onClick={() => toggle(k)}
        right={<ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />}>
        <span className="mono text-caption">{String(d.expression || "").slice(0, 90)}</span>
        <span className="mx-1.5 text-muted-foreground">→</span>
        {html ? <span className="text-foreground/90" dangerouslySetInnerHTML={{ __html: html }} /> : <span className="text-foreground/90">{d.result}</span>}
        {d.decimal && <span className="ml-2 text-caption text-muted-foreground">≈ {String(d.decimal).slice(0, 40)}</span>}
      </Row>
      {expanded && tool.result != null && (
        <pre className="mono mx-2 mb-2 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg bg-code px-3 py-2 text-caption text-foreground/70">{tool.result}</pre>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ dispatcher */

export function ToolCard(props: { msg: Msg; convId: string; index: number }) {
  const by = props.msg.tool?.by
  const time = useToolTime(props.msg, props.convId, props.index)
  const d = props.msg.tool?.display as Display | null | undefined
  // a running terminal / Python card shows its newest output line even while folded
  const folded = useApp((st) => !st.expanded[`${props.convId}:${props.index}:body`])
  const out = time.live && folded && (d?.type === "terminal" || d?.type === "python")
    ? String(d.output || "").replace(/\s+$/, "").split("\n").pop()?.trim().slice(0, 200) : ""
  const card = (
    <ToolTimeCtx.Provider value={time}>
      <ToolCardInner {...props} />
      {out ? <LastLine text={out} /> : null}
    </ToolTimeCtx.Provider>
  )
  if (!by) return card
  return (
    <div className="relative border-l-2 border-info/35 pl-1.5" title={`Run by the executor model (${by})`}>
      {card}
    </div>
  )
}

function ToolCardInner({ msg, convId, index }: { msg: Msg; convId: string; index: number }) {
  const tool = msg.tool!
  const d = tool.display || ({} as Display)
  const k = `${convId}:${index}`
  const openFile = useApp((s) => s.openFile)
  const select = useApp((s) => s.select)
  switch (d.type) {
    case "terminal":
      return <TerminalCard tool={tool} d={d} k={k} />
    case "diff":
      if (tool.ok !== false || d.pending) return <DiffCard tool={tool} d={d} k={k} convId={convId} index={index} />
      break
    case "browser": {
      const verb: Record<string, string> = { open: "Opened", read: "Read page", click: "Clicked", type: "Typed", press: "Pressed",
        select: "Selected", scroll: "Scrolled", back: "Back", forward: "Forward", reload: "Reloaded", screenshot: "Screenshot" }
      return (
        <div onClick={() => useApp.getState().setPanel({ open: true, tab: "browser" })}
          className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1 text-ui text-muted-foreground hover:bg-surface-2">
          {tool.result == null ? <Loader2 className="size-3.5 animate-spin" /> : <Globe className="size-3.5" />}
          <div className="min-w-0 flex-1 truncate">
            <span className="text-foreground/85">{verb[d.action] || d.action}</span>{" "}
            <span className="mono text-caption">{d.detail}</span>
            {d.title && <span className="text-faint"> · {d.title}</span>}
          </div>
          {d.shot && <img src={rawUrl(d.shot)} className="h-9 w-14 shrink-0 rounded border border-border object-cover object-top" />}
        </div>
      )
    }
    case "tracker": {
      const { done, total } = progress(d)
      const cur = currentItem(d)
      return (
        <Row icon={tool.result == null ? <Loader2 className="animate-spin" /> : <ListChecks />}
          onClick={() => useApp.getState().setPanel({ open: true, tab: "checklist" })}
          right={<span className="text-caption">{done}/{total}</span>}>
          Checklist {msg.tool?.result == null ? "updating" : "updated"}
          {cur && <span className="text-foreground/70"> · {cur.id} {cur.text}</span>}
        </Row>
      )
    }
    case "file":
      if (tool.ok !== false)
        return <Row icon={<FileText />} onClick={() => openFile(d.path)} right={<span className="text-caption">lines {d.start}–{d.end} of {d.total}</span>}>
          Read <span className="mono text-caption text-foreground/90">{basename(d.path)}</span></Row>
      break
    case "document":
      return <Row icon={<FileText />} onClick={() => openFile(d.path)}
        right={<span className="text-caption">{d.unit === "page" || d.unit === "slide" ? `${d.unit}s ${d.first}–${d.last} of ${d.total}` : d.kind}</span>}>
        Read document <span className="mono text-caption text-foreground/90">{basename(d.path)}</span></Row>
    case "image":
      return (
        <div className="flex items-start gap-3 rounded-card border border-line-1 bg-surface-1 p-2">
          <img src={rawUrl(d.path)} className="max-h-40 max-w-60 cursor-pointer rounded-md object-contain" onClick={() => openFile(d.path)} />
          <div className="py-1 text-caption text-muted-foreground"><ImageIcon className="mb-1 size-4" />Viewed {basename(d.path)}<br />{d.width}×{d.height}</div>
        </div>
      )
    case "bg":
      return (
        <CardShell icon={<Cpu />} tone={tool.awaiting ? "warning" : undefined}
          title={<span><span className="font-medium">Background process</span> <span className="text-muted-foreground">{d.name}</span></span>}
          right={tool.awaiting ? <StatusPill tone="warning">needs approval</StatusPill> : tool.result == null ? <StatusPill tone="info" spin>starting<Elapsed /></StatusPill>
            : <Button size="xs" variant="ghost" onClick={() => useApp.getState().setPanel({ open: true, tab: "tasks" })}>{d.id} · Tasks</Button>}>
          <div className="mono border-t border-line-1 bg-code px-(--card-px) py-2 text-caption"><span className="text-success/80">PS&gt;</span> {d.command}</div>
          <ApprovalBar tool={tool} />
        </CardShell>
      )
    case "delegate":
      return (
        <CardShell icon={<Workflow />} title={<span><span className="font-medium">Delegated</span> {d.title}</span>}
          right={d.conv_id && <Button size="xs" variant="ghost" onClick={() => select(d.conv_id)}>Open thread <ExternalLink /></Button>}>
          <div className="border-t border-line-1 px-(--card-px) py-2 text-caption text-muted-foreground line-clamp-3">{d.task}</div>
        </CardShell>
      )
    case "gather":
      if (d.running) return (
        <CardShell icon={<Workflow />} title={<span><span className="font-medium">Fan-out</span> <span className="text-muted-foreground">{(d.workers || []).length} workers started in the background</span></span>}
          right={<StatusPill tone="info" spin>working</StatusPill>}>
          <div className="border-t border-line-1 px-(--card-px) py-2 text-caption text-muted-foreground">
            {(d.workers || []).map((w: any) => w.title).join(" · ")}
            {d.mine ? <div className="mt-1"><span className="font-medium text-foreground/80">Main model's own part:</span> {d.mine}</div> : null}
          </div>
        </CardShell>
      )
      return (
        <CardShell icon={<Workflow />} title={<span><span className="font-medium">Fan-out results</span> <span className="text-muted-foreground">{(d.workers || []).length} workers</span></span>}>
          <div className="divide-y divide-line-1 border-t border-line-1">
            {(d.workers || []).map((w: any, i: number) => (
              <details key={i} className="group px-(--card-px) py-1.5 text-caption">
                <summary className="flex cursor-pointer list-none items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground/85">{w.title}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{w.steps} calls · {Math.round(w.seconds)}s</span>
                  {w.error ? <StatusPill tone="warning">{w.error}</StatusPill> : null}
                </summary>
                <div className="mt-1.5 pb-1"><Markdown text={w.report || ""} className="text-caption text-muted-foreground" /></div>
              </details>
            ))}
          </div>
        </CardShell>
      )
    case "advisor":
      return (
        <CardShell icon={<GraduationCap />} title={<span><span className="font-medium">Advisor</span> <span className="text-muted-foreground">{d.model}{d.cost != null ? ` · $${Number(d.cost).toFixed(3)}` : ""}</span></span>}>
          <div className="border-t border-line-1 px-(--card-px) py-2.5">
            <div className="mb-1.5 text-caption text-muted-foreground">{d.question}</div>
            <Markdown text={d.advice || ""} className="text-ui" />
          </div>
        </CardShell>
      )
    case "note":
      return <Row icon={<Pin />} onClick={() => useApp.getState().set({ settingsOpen: true, settingsTab: "memory" } as any)}
        right={d.path ? <PathLink path={d.path} label="memory" /> : undefined}>
        {d.action === "deleted" ? "Forgot" : d.action === "updated" ? "Updated memory" : "Remembered"}:{" "}
        <span className={cn("text-foreground/85", d.action === "deleted" && "line-through opacity-70")}>{d.text}</span></Row>
    case "math":
      return <MathCard tool={tool} d={d} k={k} />
    case "python":
      return <PythonCard tool={tool} d={d} k={k} />
    case "draft":
      return <DraftCard tool={tool} d={d} />
    case "web_hits":
      return <WebHitsCard d={d} k={k} />
    case "doc_hits":
      return (
        <CardShell icon={<Search />} title={<span>Searched your files <span className="font-normal text-muted-foreground">“{d.query}”</span></span>}
          right={<span className="text-caption text-muted-foreground">{(d.hits || []).length} files</span>}>
          <div className="divide-y divide-line-1">
            {(d.hits || []).map((h: any) => (
              <button key={h.path} onClick={() => openFile(h.path)} className="block w-full px-3 py-1.5 text-left hover:bg-surface-2">
                <div className="truncate text-caption text-foreground/90">{basename(h.path)} <span className="text-faint">{dirname(h.path)}</span></div>
                <div className="line-clamp-2 text-caption text-muted-foreground">{h.snippet}</div>
              </button>
            ))}
          </div>
        </CardShell>
      )
    case "document_created":
      if (tool.ok === false) break
      return (
        <div onClick={() => openFile(d.path)}
          className="flex cursor-pointer items-center gap-2.5 rounded-card border border-line-1 bg-surface-1 px-3 py-2 text-ui hover:bg-surface-2">
          <span className="text-muted-foreground [&_svg]:size-4"><FileText /></span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-foreground/90">{basename(d.path)}</div>
            <div className="text-caption text-muted-foreground">Created {String(d.kind).toUpperCase()} · {d.info}</div>
          </div>
          <span className="text-caption text-muted-foreground">Open ›</span>
        </div>
      )
    case "table":
      return <TableCard tool={tool} d={d} k={k} />
    case "artifact":
      if (tool.ok === false) break
      return (
        <div onClick={() => { useApp.getState().set({ artifactSel: d.id, artifactOpen: d.id } as any); showPanel("artifacts") }}
          className="flex cursor-pointer items-center gap-2.5 rounded-card border border-line-1 bg-surface-1 px-3 py-2 text-ui hover:bg-surface-2">
          <span className={cn("[&_svg]:size-4", tool.result == null ? "text-info" : "text-muted-foreground")}>{tool.result == null ? <Loader2 className="animate-spin" /> : TYPE_ICON[d.atype] || <FileText />}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-foreground/90">{d.title}</div>
            <div className="text-caption text-muted-foreground">{d.action === "created" ? "Created" : "Revised"} · {d.atype}{d.language ? ` · ${d.language}` : ""} · v{d.version}</div>
          </div>
          <span className="text-caption text-muted-foreground">Open ›</span>
        </div>
      )
  }
  if (d.type === "pending_file" && tool.result == null && !tool.awaiting)
    return <Row icon={<Loader2 className="animate-spin" />}>{tool.name.replace(/_/g, " ")} <span className="mono text-caption">{basename(d.path || "")}</span></Row>
  return <GenericCard tool={tool} k={k} />
}

/* ------------------------------------------------------------------ non-tool event cards */

export function ChangesCard({ msg, convId, index }: { msg: Msg; convId: string; index: number }) {
  const openFile = useApp((s) => s.openFile)
  const files = Object.entries(msg.files || {})
  return (
    <div className="rounded-card border border-line-1 bg-surface-1 px-(--card-px) py-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-caption font-medium">Files changed <span className="text-muted-foreground">({files.length})</span></span>
        {msg.reverts?.length ? (msg.undone ? <StatusPill tone="muted">undone</StatusPill> : (
          <Button size="xs" variant="ghost" onClick={async () => {
            if (!confirm("Undo every file change from this turn?")) return
            try {
              const r = await api.revert(convId, index)
              toast.success("Changes undone", { description: r.message })
            } catch (e: any) {
              toast.error(e.message)
            }
          }}><Undo2 /> Undo all changes from this turn</Button>)) : null}
      </div>
      <div className="space-y-0.5">
        {files.map(([p, what]) => (
          <div key={p} className="flex items-center gap-2 text-caption">
            <button className="mono text-foreground/90 hover:underline" onClick={() => openFile(p)}>{basename(p)}</button>
            <span className="text-muted-foreground">{what}</span>
            <span className="mono truncate text-meta text-faint">{dirname(p)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function CompactedDivider({ msg, k }: { msg: Msg; k: string }) {
  const expanded = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const sizes = msg.before ? ` · ${(msg.before / 1000).toFixed(1)}k → ${((msg.after || 0) / 1000).toFixed(1)}k tokens` : ""
  return (
    <div className="my-2">
      <div className="flex items-center gap-3 text-caption text-muted-foreground">
        <div className="h-px flex-1 bg-border" />
        <button onClick={() => toggle(k)} className="hover:text-foreground">Earlier conversation compressed{sizes} · {expanded ? "hide" : "show"} summary</button>
        <div className="h-px flex-1 bg-border" />
      </div>
      {expanded && <div className="mx-auto mt-2 max-w-2xl rounded-lg bg-muted/40 px-4 py-3 text-ui text-muted-foreground"><Markdown text={msg.summary || ""} /></div>}
    </div>
  )
}

export function TaskDoneCard({ msg }: { msg: Msg }) {
  const select = useApp((s) => s.select)
  const ok = msg.status === "done"
  return (
    <div data-tone={ok ? "success" : "destructive"} className="tone-edge tone-wash flex items-start gap-2 rounded-card border px-3 py-2">
      {ok ? <Check className="tone-text mt-0.5 size-4" /> : <X className="tone-text mt-0.5 size-4" />}
      <div className="min-w-0 flex-1">
        <div className="text-ui">Delegated task <span className="font-medium">{msg.title}</span> {msg.status}</div>
        {msg.result && <div className="mt-0.5 line-clamp-2 text-caption text-muted-foreground">{msg.result}</div>}
      </div>
      {msg.conv_id && <Button size="xs" variant="ghost" onClick={() => select(msg.conv_id!)}>Open thread</Button>}
    </div>
  )
}
