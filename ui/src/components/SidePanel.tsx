import { useEffect, useMemo, useState } from "react"
import CodeMirror from "@uiw/react-codemirror"
import { oneDark } from "@codemirror/theme-one-dark"
import { javascript } from "@codemirror/lang-javascript"
import { python } from "@codemirror/lang-python"
import { markdown } from "@codemirror/lang-markdown"
import { json } from "@codemirror/lang-json"
import { EditorView } from "@codemirror/view"
import {
  CheckCircle2, ChevronRight, Compass, Cpu, Globe, ListChecks, ExternalLink, FileText, FolderOpen, Loader2, RefreshCw, Save, Square, Trash2, Workflow, X, XCircle, AlertTriangle,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { ChecklistView, ProgressBar, latestChecklist, latestPlan, progress } from "./Checklist"
import { Markdown } from "./Markdown"
import { BrowserView } from "./BrowserView"
import { api, previewUrl, rawUrl } from "@/lib/api"
import { basename, dirname } from "@/lib/paths"
import { useApp } from "@/lib/store"
import type { Task } from "@/lib/types"
import { cn } from "@/lib/utils"

function dur(sec: number) {
  const s = Math.max(0, Math.floor(sec))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

/* ------------------------------------------------------------------ tasks */

function TaskIcon({ t }: { t: Task }) {
  if (t.status === "needs approval") return <AlertTriangle className="size-4 text-warning" />
  if (t.status === "running") return <Loader2 className="size-4 animate-spin text-info" />
  if (t.status === "done" || t.status === "exit 0") return <CheckCircle2 className="size-4 text-success" />
  return <XCircle className="size-4 text-muted-foreground" />
}

function BgOutput({ id, running }: { id: string; running: boolean }) {
  const [lines, setLines] = useState<string[]>([])
  useEffect(() => {
    let alive = true
    const load = () => api.bgOutput(id).then((r) => alive && setLines(r.lines)).catch(() => {})
    load()
    const t = running ? setInterval(load, 1000) : null
    return () => { alive = false; if (t) clearInterval(t) }
  }, [id, running])
  return (
    <pre className="mono h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-code p-3 text-caption leading-relaxed text-foreground/75">
      {lines.join("\n") || "(no output yet)"}
    </pre>
  )
}

export function TasksView() {
  const tasks = useApp((s) => s.tasks)
  const select = useApp((s) => s.select)
  const refresh = useApp((s) => s.refreshTasks)
  const [sel, setSel] = useState<string | null>(null)
  const now = useNow()
  useEffect(() => { refresh() }, [refresh])
  const chosen = tasks.find((t) => `${t.type}:${t.id}` === sel) || null
  const active = tasks.filter((t) => t.status === "running" || t.status === "needs approval")
  const done = tasks.filter((t) => !active.includes(t))

  const row = (t: Task) => {
    const key = `${t.type}:${t.id}`
    const Icon = t.type === "bg" ? Cpu : Workflow
    return (
      <button key={key} onClick={() => setSel(key)}
        className={cn("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left", sel === key ? "bg-muted" : "hover:bg-surface-2")}>
        <TaskIcon t={t} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 truncate text-ui"><Icon className="size-3.5 shrink-0 text-muted-foreground" />{t.title.replace(/^⇢\s*/, "")}</div>
          <div className="text-caption text-muted-foreground">{t.status} · {t.ended ? `took ${dur(t.ended - t.started)}` : dur(now - t.started)}</div>
        </div>
      </button>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <span className="text-ui font-medium">Tasks</span>
        {done.length > 0 && <Button size="xs" variant="ghost" onClick={() => api.clearTasks().then(refresh)}><Trash2 /> Clear finished</Button>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {!tasks.length && (
          <div className="px-4 py-6 text-center text-ui text-muted-foreground">
            Background processes and delegated threads appear here.
          </div>
        )}
        {active.length > 0 && <div className="px-2.5 pb-1 pt-2 text-meta uppercase tracking-wider text-muted-foreground/60">Active</div>}
        {active.map(row)}
        {done.length > 0 && <div className="px-2.5 pb-1 pt-3 text-meta uppercase tracking-wider text-muted-foreground/60">Finished</div>}
        {done.map(row)}
      </div>
      {chosen && (
        <div className="border-t border-border p-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-ui font-medium">{chosen.title.replace(/^⇢\s*/, "")}</span>
            {chosen.conv_id && chosen.type !== "bg" && <Button size="xs" variant="outline" onClick={() => select(chosen.conv_id!)}>Open thread</Button>}
            {chosen.type === "bg" && chosen.log && <Button size="xs" variant="ghost" onClick={() => useApp.getState().openFile(chosen.log!)}>Log</Button>}
            {(chosen.status === "running" || chosen.status === "needs approval") && (
              <Button size="xs" variant="destructive" onClick={() => (chosen.type === "bg" ? api.bgStop(chosen.id) : api.stop(chosen.conv_id!)).then(refresh)}>
                <Square className="fill-current" /> Stop
              </Button>
            )}
          </div>
          {chosen.type === "bg" ? (
            <>
              <div className="mono mb-2 truncate text-caption text-muted-foreground">{chosen.cwd}&gt; {chosen.command}</div>
              <BgOutput id={chosen.id} running={chosen.status === "running"} />
            </>
          ) : (
            <div className="max-h-72 space-y-2 overflow-auto text-caption">
              {chosen.tracker ? (
                <div>
                  {chosen.tracker.goal && <div className="mb-1.5 font-medium">{chosen.tracker.goal}</div>}
                  <ChecklistView t={chosen.tracker as any} compact />
                </div>
              ) : <div className="text-muted-foreground">No checklist yet.</div>}
              {chosen.result && <div className="rounded-lg bg-muted/50 p-2 text-muted-foreground">{chosen.result}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ file viewer / editor */

function langExt(path: string) {
  const ext = (path.split(".").pop() || "").toLowerCase()
  if (["js", "jsx", "ts", "tsx", "mjs"].includes(ext)) return [javascript({ jsx: true, typescript: ext.startsWith("t") })]
  if (ext === "py") return [python()]
  if (["md", "markdown"].includes(ext)) return [markdown()]
  if (["json", "jsonl"].includes(ext)) return [json()]
  return []
}

const IMG = /\.(png|jpe?g|gif|bmp|webp|svg)$/i
const OFFICE = /\.(docx?|xlsx?|xlsm|pptx?|rtf|odt|ods|odp)$/i

export function FileView({ path }: { path: string }) {
  const [data, setData] = useState<{ text: string; crlf: boolean; binary: boolean; size: number; truncated: boolean } | null>(null)
  const [doc, setDoc] = useState<string | null>(null)
  const [asText, setAsText] = useState(false)          // office files: rendered page preview, or extracted text
  const [pv, setPv] = useState<"loading" | "ok" | "fail">("loading")
  const [ver, setVer] = useState(0)
  const [text, setText] = useState("")
  const [err, setErr] = useState("")
  const [saving, setSaving] = useState(false)
  const dirty = data !== null && text !== data.text
  const kind = IMG.test(path) ? "image" : /\.pdf$/i.test(path) ? "pdf" : OFFICE.test(path) ? "office" : "text"

  const load = async () => {
    setErr("")
    try {
      if (kind === "office") {
        setPv("loading"); setVer(Date.now())
        fetch(previewUrl(path) + "&check=1").then((r) => setPv(r.ok ? "ok" : "fail")).catch(() => setPv("fail"))
        const d = await api.doc(path)
        setDoc(d.text)
      } else if (kind === "text") {
        const d = await api.file(path)
        setData(d)
        setText(d.text)
      }
    } catch (e: any) {
      setErr(e.message)
    }
  }
  useEffect(() => { setData(null); setDoc(null); load() }, [path])

  const save = async () => {
    if (!data) return
    setSaving(true)
    try {
      await api.saveFile(path, text, data.crlf)
      setData({ ...data, text })
      toast.success(`Saved ${basename(path)}`, { description: "The previous version is kept in agent_trash/backups." })
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }
  const exts = useMemo(() => [...langExt(path), EditorView.lineWrapping], [path])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5">
        <span className="mono min-w-0 flex-1 truncate text-caption text-muted-foreground" title={path}>{dirname(path)}</span>
        {kind === "office" && pv !== "fail" && <Button size="xs" variant="ghost" onClick={() => setAsText(!asText)}>{asText ? "Pages" : "Text"}</Button>}
        {kind === "text" && <Button size="xs" variant={dirty ? "default" : "ghost"} disabled={!dirty || saving} onClick={save}><Save /> Save</Button>}
        <Button size="icon-xs" variant="ghost" onClick={load} title="Reload" aria-label="Reload"><RefreshCw /></Button>
        <Button size="icon-xs" variant="ghost" onClick={() => api.open(path)} title="Open with the default app" aria-label="Open with the default app"><ExternalLink /></Button>
        <Button size="icon-xs" variant="ghost" onClick={() => api.open(path, "reveal")} title="Show in Explorer" aria-label="Show in Explorer"><FolderOpen /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto"
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save() } }}>
        {err && <div className="p-4 text-ui text-destructive">{err}</div>}
        {kind === "image" && <div className="grid h-full place-items-center p-4"><img src={rawUrl(path)} className="max-h-full max-w-full rounded-lg object-contain" /></div>}
        {kind === "pdf" && <iframe src={rawUrl(path)} className="h-full w-full border-0 bg-white" />}
        {kind === "office" && !asText && pv === "ok" && <iframe src={previewUrl(path, ver)} className="h-full w-full border-0 bg-white" />}
        {kind === "office" && !asText && pv === "loading" && <div className="flex items-center gap-2 p-6 text-caption text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Rendering pages…</div>}
        {kind === "office" && (asText || pv === "fail") && (doc === null && !err ? <Loader2 className="m-6 size-5 animate-spin text-muted-foreground" /> :
          <pre className="whitespace-pre-wrap p-4 text-ui leading-relaxed text-foreground/85">{doc}</pre>)}
        {kind === "text" && data && (data.binary ? <div className="p-4 text-ui text-muted-foreground">Binary file — use “Open with the default app”.</div> : (
          <CodeMirror value={text} onChange={setText} theme={oneDark} extensions={exts} height="100%"
            basicSetup={{ highlightActiveLine: true, foldGutter: true }} className="h-full text-ui [&_.cm-editor]:h-full [&_.cm-editor]:!bg-code [&_.cm-gutters]:!bg-code" />
        ))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ checklist */

function ApproachSection() {
  const plan = useApp((s) => (s.current && s.convs[s.current] ? latestPlan(s.convs[s.current].messages) : null))
  const [open, setOpen] = useState(false)
  if (!plan) return null
  return (
    <div className="border-b border-border px-4 py-2.5">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-left text-caption">
        <Compass className={cn("size-3.5 text-info", plan.streaming && "animate-pulse")} />
        <span className="font-medium">{plan.streaming ? "Planning…" : "Approach"}</span>
        <span className="text-meta text-muted-foreground">{plan.streaming ? "" : `${plan.tokens ?? "?"} tokens`}</span>
        <ChevronRight className={cn("ml-auto size-3.5 text-muted-foreground transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-2 max-h-[38vh] overflow-y-auto text-caption"><Markdown text={plan.content} /></div>
      )}
    </div>
  )
}

export function ChecklistPanel() {
  const t = useApp((s) => (s.current && s.convs[s.current] ? latestChecklist(s.convs[s.current].messages) : null))
  const hasPlan = useApp((s) => !!(s.current && s.convs[s.current] && latestPlan(s.convs[s.current].messages)))
  const running = useApp((s) => s.threads.find((x) => x.id === s.current)?.running ?? false)
  const openFile = useApp((s) => s.openFile)
  const [showNotes, setShowNotes] = useState(true)
  if (!t) {
    if (hasPlan) return <div className="h-full overflow-y-auto"><ApproachSection /></div>
    return (
      <div className="px-6 py-12 text-center text-ui text-muted-foreground">
        <ListChecks className="mx-auto mb-3 size-6 opacity-60" />
        The agent builds a checklist of milestones and subtasks at the start of every request.
        It shows up here and updates as the work progresses.
      </div>
    )
  }
  const { done, total, pct } = progress(t)
  const notes: string[] = t.notes || []
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-4 pb-3 pt-3.5">
        <div className="mb-1 flex items-center gap-2 text-meta uppercase tracking-wider text-muted-foreground/70">
          {running && <Loader2 className="size-3 animate-spin text-info" />} Checklist
          <span className="ml-auto normal-case tracking-normal">{done}/{total} done</span>
        </div>
        <div className="text-body font-medium leading-snug">{t.goal || "Current task"}</div>
        <ProgressBar pct={pct} className="mt-2.5" />
      </div>
      <ApproachSection />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
        <ChecklistView t={t} />
        {notes.length > 0 && (
          <div className="mt-5">
            <button onClick={() => setShowNotes(!showNotes)} className="text-meta uppercase tracking-wider text-muted-foreground/70 hover:text-foreground">
              Notes ({notes.length})
            </button>
            {showNotes && (
              <ul className="mt-1.5 space-y-1 text-caption leading-relaxed text-muted-foreground">
                {notes.slice().reverse().map((n, i) => <li key={i}>• {n}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
      {t.path && (
        <div className="border-t border-border px-4 py-2">
          <button onClick={() => openFile(t.path)} className="mono truncate text-caption text-info hover:underline" title={t.path}>
            {t.path}
          </button>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ panel */

export function SidePanel() {
  const panel = useApp((s) => s.panel)
  const files = useApp((s) => s.files)
  const activeFile = useApp((s) => s.activeFile)
  const setPanel = useApp((s) => s.setPanel)
  const closeFile = useApp((s) => s.closeFile)
  const set = useApp((s) => s.set)
  const tasks = useApp((s) => s.tasks)
  const activeCount = tasks.filter((t) => t.status === "running" || t.status === "needs approval").length
  const cl = useApp((s) => (s.current && s.convs[s.current] ? latestChecklist(s.convs[s.current].messages) : null))
  return (
    <div className="flex h-full flex-col border-l border-border bg-surface">
      <div className="flex items-center gap-0.5 overflow-x-auto border-b border-border px-2 pt-1.5">
        <button onClick={() => setPanel({ tab: "checklist" })}
          className={cn("flex items-center gap-1.5 rounded-t-lg px-3 py-1.5 text-caption", panel.tab === "checklist" ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground")}>
          <ListChecks className="size-3.5" /> Checklist {cl && <span className="text-meta text-muted-foreground">{progress(cl).done}/{progress(cl).total}</span>}
        </button>
        <button onClick={() => setPanel({ tab: "browser" })}
          className={cn("flex items-center gap-1.5 rounded-t-lg px-3 py-1.5 text-caption", panel.tab === "browser" ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground")}>
          <Globe className="size-3.5" /> Browser
        </button>
        <button onClick={() => setPanel({ tab: "tasks" })}
          className={cn("flex items-center gap-1.5 rounded-t-lg px-3 py-1.5 text-caption", panel.tab === "tasks" ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground")}>
          <Workflow className="size-3.5" /> Tasks {activeCount > 0 && <span data-tone="info" className="tone-soft tone-text rounded-full px-1.5 text-meta">{activeCount}</span>}
        </button>
        {files.map((f) => (
          <div key={f} className={cn("group flex items-center rounded-t-lg", panel.tab === "file" && activeFile === f ? "bg-background" : "")}>
            <button onClick={() => set({ activeFile: f, panel: { open: true, tab: "file" } })}
              className={cn("flex max-w-44 items-center gap-1.5 py-1.5 pl-3 pr-1 text-caption", panel.tab === "file" && activeFile === f ? "text-foreground" : "text-muted-foreground hover:text-foreground")} title={f}>
              <FileText className="size-3.5 shrink-0" /><span className="truncate">{basename(f)}</span>
            </button>
            <button onClick={() => closeFile(f)} className="mr-1 rounded p-0.5 text-muted-foreground opacity-60 hover:bg-surface-2 hover:opacity-100"><X className="size-3" /></button>
          </div>
        ))}
        <div className="flex-1" />
        <Button size="icon-xs" variant="ghost" className="mb-1" onClick={() => setPanel({ open: false })} title="Close panel" aria-label="Close panel"><X /></Button>
      </div>
      <div className="min-h-0 flex-1 bg-background">
        {panel.tab === "browser" ? <BrowserView /> : panel.tab === "checklist" ? <ChecklistPanel /> : panel.tab === "tasks" || !activeFile ? <TasksView /> : <FileView key={activeFile} path={activeFile} />}
      </div>
    </div>
  )
}
