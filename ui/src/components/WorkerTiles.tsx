import { useEffect, useState } from "react"
import { Bot, Cpu } from "lucide-react"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Markdown } from "./Markdown"
import { useApp } from "@/lib/store"
import type { Conv, Worker } from "@/lib/types"
import { cn } from "@/lib/utils"

/** Blue (pulsing) while working, green when done, amber on error/stop, grey while queued. */
function Dot({ state }: { state: string }) {
  const c = state === "running" ? "bg-info" : state === "done" ? "bg-success" : state === "queued" ? "bg-muted-foreground/40" : "bg-warning"
  return (
    <span className="relative flex size-2 shrink-0">
      {state === "running" && <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", c)} />}
      <span className={cn("relative inline-flex size-2 rounded-full", c)} />
    </span>
  )
}

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(t)
  }, [active])
  return now
}

const secs = (w: Worker, now: number) => (w.started ? Math.max(0, (w.ended || now) - w.started) : 0)
const fmt = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`)

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-1.5">
      <div className="text-meta uppercase tracking-wide text-muted-foreground/70">{label}</div>
      <div className="text-ui tabular-nums text-foreground/90">{value}</div>
    </div>
  )
}

function WorkerModal({ w, model, onClose }: { w: Worker; model: string; onClose: () => void }) {
  const now = useNow(w.status === "running")
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogTitle className="flex items-center gap-2 text-chat"><Dot state={w.status} /> Worker {w.i + 1}: {w.title}</DialogTitle>
        <div className="grid grid-cols-4 gap-2">
          <Stat label="Status" value={w.status} />
          <Stat label="Speed" value={w.tps ? `${w.tps} tok/s` : "—"} />
          <Stat label="Tokens" value={w.tokens ?? 0} />
          <Stat label="Elapsed" value={fmt(secs(w, now))} />
        </div>
        <div className="text-caption text-muted-foreground">Model: {model} (CPU)</div>
        <div>
          <div className="mb-1 text-meta font-medium uppercase tracking-wide text-muted-foreground/70">Brief</div>
          <div className="whitespace-pre-wrap rounded-lg bg-muted/30 px-3 py-2 text-ui text-foreground/85">{w.brief}</div>
        </div>
        {w.status === "running" && w.action && (
          <div className="text-ui"><span className="text-muted-foreground">Now: </span><span className="mono text-caption">{w.action}</span></div>
        )}
        <div>
          <div className="mb-1 text-meta font-medium uppercase tracking-wide text-muted-foreground/70">Tool calls ({w.steps?.length || 0})</div>
          <div className="space-y-1">
            {(w.steps || []).map((s, i) => (
              <details key={i} className="rounded-lg border border-border/50 px-3 py-1.5 text-caption">
                <summary className="flex cursor-pointer list-none items-center gap-2">
                  <Dot state={s.secs == null ? "running" : s.ok === false ? "error" : "done"} />
                  <span className="font-medium">{s.name}</span>
                  <span className="mono min-w-0 flex-1 truncate text-caption text-muted-foreground">{s.args}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{s.secs == null ? "…" : `${s.secs}s`}</span>
                </summary>
                {s.preview && <div className="mt-1.5 whitespace-pre-wrap text-caption text-muted-foreground">{s.preview}</div>}
              </details>
            ))}
            {!w.steps?.length && <div className="text-caption text-muted-foreground">No tool calls yet.</div>}
          </div>
        </div>
        {w.report && (
          <div>
            <div className="mb-1 text-meta font-medium uppercase tracking-wide text-muted-foreground/70">Report</div>
            <Markdown text={w.report} className="text-ui" />
          </div>
        )}
        {w.error && w.error !== "stopped" && <div className="text-caption text-warning">{w.error}</div>}
      </DialogContent>
    </Dialog>
  )
}

function MainModal({ conv, onClose }: { conv: Conv; onClose: () => void }) {
  const tps = useApp((s) => s.tps[conv.id])
  const status = useApp((s) => s.status[conv.id])
  const ctx = useApp((s) => s.ctx[conv.id])
  const model = useApp((s) => s.settings?.agent?.model || "")
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogTitle className="flex items-center gap-2 text-chat"><Dot state={conv.running ? "running" : "done"} /> Main agent</DialogTitle>
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Status" value={conv.running ? "working" : "done"} />
          <Stat label="Speed" value={tps ? `${tps} tok/s` : "—"} />
          <Stat label="Context" value={ctx?.max ? `${Math.round(ctx.used / 1000)}k / ${Math.round(ctx.max / 1000)}k` : "—"} />
        </div>
        <div className="text-caption text-muted-foreground">Model: {String(model).split(/[\\/]/).pop()} (GPU)</div>
        {conv.running && status && <div className="text-ui"><span className="text-muted-foreground">Now: </span>{status}</div>}
      </DialogContent>
    </Dialog>
  )
}

/** Fan-out tiles just above the composer: the main agent plus each parallel worker. */
export function WorkerTiles({ conv }: { conv: Conv }) {
  const items = conv.workers?.items || []
  const tps = useApp((s) => s.tps[conv.id])
  const status = useApp((s) => s.status[conv.id])
  const [open, setOpen] = useState<number | "main" | null>(null)
  const now = useNow(items.some((w) => w.status === "running"))
  if (!items.length) return null
  const sel = typeof open === "number" ? items.find((w) => w.i === open) : null
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-1.5">
      <div className="flex gap-1.5 overflow-x-auto">
        <button onClick={() => setOpen("main")}
          className="flex min-w-36 flex-1 flex-col gap-0.5 rounded-lg border border-border/70 bg-surface-1 px-2.5 py-1.5 text-left hover:bg-surface-2">
          <span className="flex items-center gap-1.5 text-caption font-medium"><Dot state={conv.running ? "running" : "done"} /><Bot className="size-3.5 text-muted-foreground" /> Main agent</span>
          <span className="truncate text-meta text-muted-foreground">{conv.running ? (tps ? `${tps} tok/s · ` : "") + (status || "working") : "done"}</span>
        </button>
        {items.map((w) => (
          <button key={w.i} onClick={() => setOpen(w.i)}
            className="flex min-w-36 flex-1 flex-col gap-0.5 rounded-lg border border-border/70 bg-surface-1 px-2.5 py-1.5 text-left hover:bg-surface-2">
            <span className="flex min-w-0 items-center gap-1.5 text-caption font-medium"><Dot state={w.status} /><Cpu className="size-3.5 shrink-0 text-muted-foreground" /><span className="truncate">{w.title}</span></span>
            <span className="truncate text-meta text-muted-foreground">
              {w.status === "running" ? `${w.tps ? `${w.tps} tok/s · ` : ""}${w.steps?.length || 0} calls · ${fmt(secs(w, now))}`
                : w.status === "queued" ? "queued" : `${w.status} · ${w.steps?.length || 0} calls · ${fmt(secs(w, now))}`}
            </span>
          </button>
        ))}
      </div>
      {open === "main" && <MainModal conv={conv} onClose={() => setOpen(null)} />}
      {sel && <WorkerModal w={sel} model={conv.workers?.model || ""} onClose={() => setOpen(null)} />}
    </div>
  )
}
