import { useEffect, useState } from "react"
import { Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MeterBar } from "@/components/MeterBar"

/** Nothing downloads without a yes. Two kinds of question share this modal:
 *  - downloads the UI starts (a model): confirmDownload({...}) resolves true/false when the user answers;
 *  - downloads the backend needs (the speech model, the memory model): backend/downloads.py asks, this polls
 *    /api/downloads and shows each open question, then the download's progress once accepted.
 *  Updates have their own prompt (UpdateChip), built the same way. */

export type DownloadAsk = { title: string; detail?: string; size?: number; source?: string; action?: string }
type Pending = DownloadAsk & { resolve: (ok: boolean) => void }
type ServerReq = DownloadAsk & { id: string; kind: string; status: string; done: number; total: number; error?: string | null }

let queue: Pending[] = []
const listeners = new Set<() => void>()
const changed = () => listeners.forEach((f) => f())

/** Ask before a download the UI starts. -> true when the user chose to download. */
export function confirmDownload(ask: DownloadAsk): Promise<boolean> {
  return new Promise((resolve) => { queue = [...queue, { ...ask, resolve }]; changed() })
}

export const fmtBytes = (n?: number) => !n ? "" : n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(1)} GB` : `${Math.max(1, Math.round(n / (1 << 20)))} MB`

export function ConsentCenter() {
  const [, tick] = useState(0)
  const [server, setServer] = useState<ServerReq[]>([])
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  useEffect(() => { const f = () => tick((x) => x + 1); listeners.add(f); return () => { listeners.delete(f) } }, [])
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const d = await (await fetch("/api/downloads")).json()
        if (alive) setServer(d.requests || [])
      } catch { /* server restarting */ }
      if (alive) timer = setTimeout(poll, 1500)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  const local = queue[0]
  const answerLocal = (ok: boolean) => { const p = queue[0]; queue = queue.slice(1); changed(); p?.resolve(ok) }
  const asking = server.find((r) => r.status === "asking")
  const running = server.find((r) => (r.status === "downloading" || r.status === "failed") && !hidden.has(r.id))
  const answerServer = async (id: string, accept: boolean) => {
    await fetch("/api/downloads/answer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, accept }) }).catch(() => {})
    setServer((s) => s.map((r) => (r.id === id ? { ...r, status: accept ? "downloading" : "declined" } : r)))
  }

  const ask: DownloadAsk | null = local || asking || null
  if (ask) {
    const onAnswer = (ok: boolean) => (local ? answerLocal(ok) : answerServer((asking as ServerReq).id, ok))
    return (
      <Dialog open onOpenChange={(o) => { if (!o) onAnswer(false) }}>
        <DialogContent className="sm:max-w-md" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><Download className="size-4 text-muted-foreground" /> Download {ask.title}?</DialogTitle>
            {ask.detail && <DialogDescription className="text-ui leading-relaxed">{ask.detail}</DialogDescription>}
          </DialogHeader>
          <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1 rounded-card border border-line-1 bg-surface-1 px-3 py-2 text-caption">
            <dt className="text-faint">Size</dt><dd className="mono tabular-nums">{fmtBytes(ask.size) || "shown once it starts"}</dd>
            {ask.source && <><dt className="text-faint">From</dt><dd className="mono truncate" title={ask.source}>{ask.source}</dd></>}
          </dl>
          <DialogFooter>
            <Button variant="ghost" onClick={() => onAnswer(false)}>Decline</Button>
            <Button autoFocus onClick={() => onAnswer(true)}><Download /> {ask.action || "Download"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }
  if (running) {
    const pct = running.total ? Math.min(100, (100 * running.done) / running.total) : 0
    const failed = running.status === "failed"
    return (
      <Dialog open onOpenChange={(o) => { if (!o) setHidden((h) => new Set(h).add(running.id)) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{failed ? "Download failed" : `Downloading ${running.title}`}</DialogTitle>
            <DialogDescription className="text-ui">{failed ? running.error : "You can keep working; this closes when it's done."}</DialogDescription>
          </DialogHeader>
          {!failed && <>
            <MeterBar pct={pct} tone="info" label="Download progress" className="h-1.5 w-full bg-surface-1" />
            <div className="mono flex justify-between text-caption tabular-nums text-muted-foreground">
              <span>{fmtBytes(running.done) || "starting…"}{running.total ? ` of ${fmtBytes(running.total)}` : ""}</span>
              <span>{running.total ? `${Math.round(pct)}%` : ""}</span>
            </div>
          </>}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setHidden((h) => new Set(h).add(running.id))}>{failed ? "Close" : "Hide"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }
  return null
}
