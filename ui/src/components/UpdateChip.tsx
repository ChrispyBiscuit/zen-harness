import { useEffect, useState } from "react"
import { ArrowDownToLine, Loader2, RotateCcw } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

/** Updates from git (backend/updater.py): the app follows its GitHub repo's branch and offers new commits. The chip
 *  shows only when an update is ready (or one is installing); its panel lists what changed and installs it, which
 *  restarts the server (never during a run). The page reloads itself once the new server answers. */

type Commit = { sha: string; subject: string; when: number }
type Status = {
  installed?: string; installed_short?: string; latest_short?: string; available?: boolean; commits?: Commit[]
  busy?: string | null; error?: string | null; run_busy?: boolean; can_rollback?: boolean; last_check?: number
  rolled_back?: { from: string; reason: string; at: number } | null
}

const ago = (t?: number) => {
  if (!t) return ""
  const s = Date.now() / 1000 - t
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
    : `${Math.round(s / 86400)} d ago`
}

async function post(path: string) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
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

export function UpdateChip() {
  const [st, setSt] = useState<Status>({})
  const [working, setWorking] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { const d = await (await fetch("/api/update/status")).json(); if (alive) setSt(d) } catch { /* restarting */ }
      if (alive) timer = setTimeout(poll, 60000)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  const install = async (kind: "apply" | "rollback") => {
    setErr(null)
    setWorking(kind === "apply" ? "Building and testing the new version…" : "Restoring the previous version…")
    try {
      const before = (await (await fetch("/api/build")).json()).started
      const d = await post(`/api/update/${kind}`)
      if (!d.ok) { setErr(d.error || "it did not work"); setWorking(null); return }
      setWorking("Restarting…")
      await reloadWhenBack(before)
    } catch (e: any) { setErr(String(e)); setWorking(null) }
  }
  const check = async () => {
    setWorking("Checking…")
    try { setSt({ ...(await post("/api/update/check")) }) } catch { /* offline */ }
    setWorking(null)
  }

  const show = st.available || st.busy || working || st.rolled_back
  if (!show) return null
  const commits = st.commits || []
  return (
    <Popover>
      <PopoverTrigger className="flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 text-info transition-ui hover:bg-surface-2"
        title="A newer version of the app is available">
        {working || st.busy ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowDownToLine className="size-3.5" />}
        <span>{working || st.busy ? "Updating" : st.available ? `Update · ${commits.length} change${commits.length === 1 ? "" : "s"}` : "Rolled back"}</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="border-b border-line-1 px-3 py-2">
          <div className="text-ui font-medium text-foreground">
            {st.available ? "Update available" : "Previous version restored"}
          </div>
          <div className="mono text-meta text-muted-foreground">
            {st.installed_short || "unknown"}{st.available ? ` → ${st.latest_short}` : ""}
            {st.last_check ? ` · checked ${ago(st.last_check)}` : ""}
          </div>
          {st.rolled_back && <div className="mt-1 text-meta text-warning">Rolled back: {st.rolled_back.reason}</div>}
        </div>
        {st.available && (
          <ul className="max-h-64 overflow-y-auto px-3 py-2">
            {commits.map((c) => (
              <li key={c.sha} className="py-0.5 text-meta text-foreground/85" title={c.sha}>
                {c.subject} <span className="text-faint">· {ago(c.when)}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-2 border-t border-line-1 px-3 py-2">
          {st.available && (
            <button disabled={!!working || !!st.busy || st.run_busy} onClick={() => install("apply")}
              className="rounded-md bg-primary px-2.5 py-1 text-ui text-primary-foreground transition-ui hover:opacity-90 disabled:opacity-40">
              Install and restart
            </button>
          )}
          {st.can_rollback && (
            <button disabled={!!working || st.run_busy} onClick={() => install("rollback")}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-ui text-muted-foreground transition-ui hover:bg-surface-2 hover:text-foreground disabled:opacity-40"
              title="Put the version before the last update back">
              <RotateCcw className="size-3.5" /> Previous version
            </button>
          )}
          <div className="flex-1" />
          <button disabled={!!working} onClick={check} className="text-meta text-muted-foreground hover:text-foreground disabled:opacity-40">
            Check again
          </button>
        </div>
        {(working || st.run_busy || err || st.error) && (
          <div className="border-t border-line-1 px-3 py-2 text-meta">
            {working && <div className="text-info">{working}</div>}
            {!working && st.run_busy && st.available && <div className="text-muted-foreground">An agent run is going: install when it finishes.</div>}
            {(err || st.error) && <div className="text-destructive">{err || st.error}</div>}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
