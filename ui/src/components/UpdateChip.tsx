import { useEffect, useRef, useState } from "react"
import { ArrowDownToLine, Check, Loader2, Minus, RotateCcw, X } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MeterBar } from "@/components/MeterBar"

/** Updates from git (backend/updater.py): the app follows its GitHub repo's branch and offers new commits.
 *  - Automatic updates (Settings → General → Updates, on by default): the backend checks in the background and, when
 *    a new version is found, this asks in a modal: Install now, or Not now (not asked again for that version).
 *    Nothing installs without a yes.
 *  - Installing shows each step (download, UI packages, build, Python packages, test, install) with a progress bar,
 *    then the server restarts (never during a run) and the page reloads itself on the new version.
 *  - The chip in the status bar stays while an update waits, for installing it later or going back a version. */

type Commit = { sha: string; subject: string; when: number }
type Step = { key: string; label: string; typical: number; state: "pending" | "running" | "done" | "skipped" | "failed" }
type Status = {
  installed?: string; installed_short?: string; latest?: string; latest_short?: string; available?: boolean
  commits?: Commit[]; busy?: string | null; error?: string | null; run_busy?: boolean; can_rollback?: boolean
  last_check?: number; rolled_back?: { from: string; reason: string; at: number } | null
  declined?: string | null; auto?: boolean
  progress?: { steps: Step[]; current: string | null; step_started?: number; started?: number; frac?: number | null } | null
}

const ago = (t?: number) => {
  if (!t) return ""
  const s = Date.now() / 1000 - t
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
    : `${Math.round(s / 86400)} d ago`
}

async function post(path: string, body: object = {}) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  return r.json()
}

/** Wait for the restarted server (a new `started` in /api/build), then reload the page onto the new UI. */
async function reloadWhenBack(before: number) {
  for (let i = 0; i < 240; i++) {
    await new Promise((r) => setTimeout(r, 1000))
    try {
      const b = await (await fetch("/api/build")).json()
      if (b.started && b.started !== before) { location.reload(); return }
    } catch { /* restarting */ }
  }
}

/** The install's progress, 0-100: finished and skipped steps count whole; the running one creeps towards its end by
 *  its typical time (never past 90% of it); the restart is the last 8%. */
function installPct(p: Status["progress"], restarting: boolean) {
  if (restarting) return 96
  if (!p) return 2
  const steps = p.steps.filter((s) => s.state !== "skipped")
  const total = steps.reduce((a, s) => a + s.typical, 0) || 1
  let done = 0
  for (const s of steps) {
    if (s.state === "done") done += s.typical
    else if (s.state === "running") {
      if (p.frac != null) { done += s.typical * Math.min(1, p.frac); continue }      // a measured step (the file copy)
      const t = Date.now() / 1000 - (p.step_started || Date.now() / 1000)
      done += s.typical * 0.9 * (1 - Math.exp(-1.6 * t / Math.max(1, s.typical)))
    }
  }
  return Math.max(2, Math.min(92, (92 * done) / total))
}

export function UpdateChip() {
  const [st, setSt] = useState<Status>({})
  const [working, setWorking] = useState<"apply" | "rollback" | null>(null)
  const [restarting, setRestarting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [prompt, setPrompt] = useState(false)
  const [queued, setQueued] = useState(false)             // "install when the run finishes"
  const [, frame] = useState(0)
  const asked = useRef<string | null>(null)

  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { const d = await (await fetch("/api/update/status")).json(); if (alive) setSt(d) } catch { /* restarting */ }
      if (alive) timer = setTimeout(poll, working ? 500 : 30000)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [working])
  useEffect(() => {                                          // the progress bar creeps between status polls
    if (!working) return
    const t = setInterval(() => frame((x) => x + 1), 120)
    return () => clearInterval(t)
  }, [working])

  // a new version was found and automatic updates are on: ask (once per version; "Not now" remembers it)
  useEffect(() => {
    if (st.available && st.auto !== false && st.latest && st.latest !== st.declined && !working && !st.busy
        && asked.current !== st.latest) {
      asked.current = st.latest
      setPrompt(true)
    }
  }, [st.available, st.auto, st.latest, st.declined, working, st.busy])

  const install = async (kind: "apply" | "rollback") => {
    setErr(null); setPrompt(false); setQueued(false)
    setWorking(kind)
    try {
      const before = (await (await fetch("/api/build")).json()).started
      const d = await post(`/api/update/${kind}`)
      if (!d.ok) { setErr(d.error || "it did not work"); setWorking(null); return }
      setRestarting(true)
      await reloadWhenBack(before)
    } catch (e: any) { setErr(String(e)); setWorking(null) }
  }
  // queued until the current agent run finishes
  useEffect(() => { if (queued && !st.run_busy && !working) install("apply") }, [queued, st.run_busy, working])

  const notNow = async () => {
    setPrompt(false)
    if (st.latest) await post("/api/update/decline", { sha: st.latest }).catch(() => {})
    setSt((s) => ({ ...s, declined: s.latest }))
  }
  const check = async () => {
    try { setSt({ ...(await post("/api/update/check")) }) } catch { /* offline */ }
  }

  const commits = st.commits || []
  const changes = (
    <ul className="max-h-56 overflow-y-auto rounded-card border border-line-1 bg-surface-1 px-3 py-2">
      {commits.map((c) => (
        <li key={c.sha} className="py-0.5 text-caption text-foreground/85" title={c.sha}>
          {c.subject} <span className="text-faint">· {ago(c.when)}</span>
        </li>
      ))}
    </ul>
  )

  const promptModal = prompt && st.available && !working && (
    <Dialog open onOpenChange={(o) => { if (!o) notNow() }}>
      <DialogContent className="sm:max-w-lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><ArrowDownToLine className="size-4 text-muted-foreground" /> An update is available</DialogTitle>
          <DialogDescription className="text-ui">
            <span className="mono">{st.installed_short || "current"} → {st.latest_short}</span> · {commits.length} change{commits.length === 1 ? "" : "s"}.
            Installing downloads and tests the new version, then restarts the app's server (about a minute).
          </DialogDescription>
        </DialogHeader>
        {changes}
        {st.run_busy && <div className="text-caption text-muted-foreground">An agent run is going: it can install as soon as the run finishes.</div>}
        <DialogFooter>
          <Button variant="ghost" onClick={notNow}>Not now</Button>
          {st.run_busy
            ? <Button autoFocus onClick={() => { setQueued(true); setPrompt(false) }}>Install after this run</Button>
            : <Button autoFocus onClick={() => install("apply")}><ArrowDownToLine /> Install now</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  const steps = st.progress?.steps || []
  const pct = installPct(st.progress, restarting)
  const progressModal = working && (
    <Dialog open onOpenChange={() => { /* stays until the app restarts */ }}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{working === "apply" ? "Installing the update" : "Restoring the previous version"}</DialogTitle>
          <DialogDescription className="text-ui">
            {restarting ? "Restarting the app's server… the window reloads by itself." : err ? err : "This takes about a minute. Your chats and settings are not touched."}
          </DialogDescription>
        </DialogHeader>
        <MeterBar pct={pct} tone={err ? "destructive" : "info"} label="Install progress" className="h-1.5 w-full bg-surface-1" />
        {working === "apply" && steps.length > 0 && (
          <ol className="space-y-1 text-caption">
            {steps.map((s) => (
              <li key={s.key} className={s.state === "skipped" ? "text-faint line-through decoration-line-3"
                : s.state === "running" ? "text-foreground" : s.state === "done" ? "text-muted-foreground" : "text-faint"}>
                <span className="mr-2 inline-grid size-3.5 place-items-center align-[-2px]">
                  {s.state === "done" ? <Check className="size-3.5 text-success" /> : s.state === "running" ? <Loader2 className="size-3.5 animate-spin text-info" />
                    : s.state === "failed" ? <X className="size-3.5 text-destructive" /> : s.state === "skipped" ? <Minus className="size-3" /> : null}
                </span>
                {s.label}{s.state === "skipped" ? " (not needed)" : ""}
              </li>
            ))}
            <li className={restarting ? "text-foreground" : "text-faint"}>
              <span className="mr-2 inline-grid size-3.5 place-items-center align-[-2px]">{restarting && <Loader2 className="size-3.5 animate-spin text-info" />}</span>
              Restarting
            </li>
          </ol>
        )}
        {err && (
          <DialogFooter><Button variant="ghost" onClick={() => { setWorking(null); setErr(null) }}>Close</Button></DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )

  const show = st.available || st.busy || working || st.rolled_back
  return (
    <>
      {promptModal}
      {progressModal}
      {show && (
        <Popover>
          <PopoverTrigger className="flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 text-info transition-ui hover:bg-surface-2"
            title="A newer version of the app is available">
            {working || st.busy ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowDownToLine className="size-3.5" />}
            <span>{working || st.busy ? "Updating" : queued ? "Update after this run" : st.available ? `Update · ${commits.length} change${commits.length === 1 ? "" : "s"}` : "Rolled back"}</span>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-96 p-0">
            <div className="border-b border-line-1 px-3 py-2">
              <div className="text-ui font-medium text-foreground">{st.available ? "Update available" : "Previous version restored"}</div>
              <div className="mono text-meta text-muted-foreground">
                {st.installed_short || "unknown"}{st.available ? ` → ${st.latest_short}` : ""}
                {st.last_check ? ` · checked ${ago(st.last_check)}` : ""}
              </div>
              {st.rolled_back && <div className="mt-1 text-meta text-warning">Rolled back: {st.rolled_back.reason}</div>}
            </div>
            {st.available && <div className="px-3 py-2">{changes}</div>}
            <div className="flex items-center gap-2 border-t border-line-1 px-3 py-2">
              {st.available && (
                <Button size="sm" disabled={!!working || !!st.busy} onClick={() => setPrompt(true)}>Install…</Button>
              )}
              {st.can_rollback && (
                <Button size="sm" variant="ghost" disabled={!!working || st.run_busy} onClick={() => install("rollback")}
                  title="Put the version before the last update back">
                  <RotateCcw /> Previous version
                </Button>
              )}
              <div className="flex-1" />
              <button disabled={!!working} onClick={check} className="text-meta text-muted-foreground hover:text-foreground disabled:opacity-40">
                Check again
              </button>
            </div>
            {(err || st.error) && !working && (
              <div className="border-t border-line-1 px-3 py-2 text-meta text-destructive">{err || st.error}</div>
            )}
          </PopoverContent>
        </Popover>
      )}
    </>
  )
}

/** Settings → General → Updates: the switch lives in settings.update.auto_check; this shows the version and checks now. */
export function UpdateStatusLine() {
  const [st, setSt] = useState<Status>({})
  const [busy, setBusy] = useState(false)
  useEffect(() => { fetch("/api/update/status").then((r) => r.json()).then(setSt).catch(() => {}) }, [])
  const check = async () => {
    setBusy(true)
    try { setSt(await post("/api/update/check")) } catch { /* offline */ }
    setBusy(false)
  }
  return (
    <div className="flex items-center gap-3 text-caption text-muted-foreground">
      <span className="mono">version {st.installed_short || "unknown"}</span>
      <span>{st.available ? `update ${st.latest_short} available` : st.last_check ? `up to date · checked ${ago(st.last_check)}` : ""}</span>
      <div className="flex-1" />
      <Button size="sm" variant="outline" disabled={busy} onClick={check}>{busy ? <Loader2 className="animate-spin" /> : null} Check now</Button>
    </div>
  )
}
