import { useEffect, useState } from "react"
import { AlarmClock, ChevronUp, PanelLeftClose, Pause, Play, Plus, Trash2, Zap } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"

/** Scheduled agent tasks, at the bottom of the sidebar: a bottom row (count, show/hide, add, and the sidebar's own
 *  fold button at the right), with the list and the add form opening upward above it. Runs are time-based and
 *  recurring (web-agent/scheduler.py): daily, weekdays, chosen days, several times a day, every N days, monthly,
 *  every N minutes/hours, or once. Each run starts its own chat. The agent can create, change, pause and delete
 *  these too (schedule_task, update_schedule, delete_schedule). */

type Sched = { id: string; name: string; prompt: string; when: string; enabled: boolean; next_run: number | null; last_run: number | null; last_conv: string | null; runs: number; created_by: string }

const inputCls = "w-full rounded-md border border-input bg-transparent px-2 py-1.5 text-caption outline-none focus:border-ring"
const EXAMPLES = ["weekdays 8am", "daily 9am and 5pm", "every monday at 9am", "every 3 days at 7am", "monthly 1 9am", "every 2h"]
const OPEN_KEY = "zen.sched.open"

/** "in 20 min", "today 5:30 PM", "tmrw 8:00 AM", "Mon 9:00 AM", "Oct 31 9:00 AM" */
function when(ts: number | null) {
  if (!ts) return "—"
  const d = new Date(ts * 1000), now = new Date(), mins = Math.round((d.getTime() - now.getTime()) / 60000)
  if (mins < 60) return mins <= 1 ? "in a minute" : `in ${mins} min`
  const hm = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) / 86400000)
  if (days === 0) return `today ${hm}`
  if (days === 1) return `tmrw ${hm}`
  if (days < 7) return `${d.toLocaleDateString([], { weekday: "short" })} ${hm}`
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`
}

export function Schedules() {
  const items = useApp((s) => (s as any).schedules as Sched[] | undefined)
  const select = useApp((s) => s.select)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(OPEN_KEY) === "1" } catch { return false } })
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: "", prompt: "", when: "weekdays 8am" })
  const load = () => fetch("/api/schedules").then((r) => r.json()).then((d) => { if (Array.isArray(d.items)) useApp.setState({ schedules: d.items } as any) }).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => { try { localStorage.setItem(OPEN_KEY, open ? "1" : "0") } catch { /* blocked */ } }, [open])
  const call = async (method: string, url: string, body?: any) => {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
      .then((x) => x.json()).catch(() => ({ ok: false, error: "The app isn't reachable" }))
    if (r.ok === false) toast.error(r.error || "Failed")
    load()
    return r
  }
  const add = async () => {
    const r = await call("POST", "/api/schedules", form)
    if (r.ok) { setAdding(false); setForm({ name: "", prompt: "", when: "weekdays 8am" }); setOpen(true) }
  }
  const list = items || []
  const active = list.filter((s) => s.enabled)
  const soonest = active.reduce<Sched | null>((a, s) => (s.next_run && (!a || (a.next_run ?? Infinity) > s.next_run) ? s : a), null)
  const iconBtn = "grid size-7 place-items-center rounded-md text-muted-foreground transition-ui hover:bg-surface-2 hover:text-foreground"

  return (
    <div className="shrink-0 border-t border-sidebar-border" data-density-scope>
      {(open || adding) && (
        <div className="enter-rise max-h-[42vh] overflow-y-auto px-2 pt-2">
          {adding && (
            <div className="mb-2 space-y-1.5 rounded-lg border border-line-1 bg-surface-1 p-2">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Name (e.g. Morning news brief)" className={inputCls} />
              <textarea rows={3} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })}
                placeholder="What should the agent do each time? (it starts fresh, so be complete)" className={inputCls} />
              <input value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })} aria-label="When"
                placeholder="weekdays 8am · daily 9am and 5pm · monthly 1 9am · every 2h" className={inputCls + " mono"} />
              <div className="flex flex-wrap gap-1">
                {EXAMPLES.map((x) => (
                  <button key={x} onClick={() => setForm({ ...form, when: x })}
                    className={cn("rounded-chip border px-1.5 py-0.5 text-meta transition-ui",
                      form.when === x ? "border-line-3 bg-surface-3 text-foreground" : "border-line-1 text-muted-foreground hover:text-foreground")}>{x}</button>
                ))}
              </div>
              <div className="flex justify-end gap-1.5 pt-0.5">
                <Button size="xs" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
                <Button size="xs" onClick={add} disabled={!form.prompt.trim() || !form.when.trim()}>Schedule</Button>
              </div>
            </div>
          )}
          {open && !list.length && !adding && (
            <div className="px-1 pb-2 text-caption text-muted-foreground">
              Nothing scheduled. Add one, or ask the agent: “every weekday at 8am, summarise the news on …”
            </div>
          )}
          {open && list.map((s) => (
            <div key={s.id} className={cn("group relative flex items-start gap-2 rounded-lg px-1.5 py-(--row-py) transition-ui hover:bg-surface-1", !s.enabled && "opacity-60")}>
              <AlarmClock data-tone={s.enabled ? "info" : "muted"} className="tone-text mt-0.5 size-3.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-caption font-medium text-sidebar-foreground/90" title={s.prompt}>{s.name}</div>
                <div className="truncate text-meta text-muted-foreground" title={s.when}>{s.when.replace(" at ", " ")}</div>
                <div className="flex min-w-0 items-center gap-2 whitespace-nowrap text-meta">
                  <span className={cn("min-w-0 truncate", s.enabled ? "text-foreground/70" : "text-muted-foreground")}>{s.enabled ? `next ${when(s.next_run)}` : "paused"}</span>
                  {s.last_conv && <button onClick={() => select(s.last_conv!)} className="shrink-0 text-info hover:underline">{s.runs > 1 ? `${s.runs} runs` : "last run"} ›</button>}
                </div>
              </div>
              {/* actions float over the row on hover/focus, so they take no room from the text */}
              <div className="absolute right-1 top-1 flex rounded-md bg-surface-2 opacity-0 shadow-elev-2 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                <button title="Run now" aria-label={`Run ${s.name} now`} onClick={() => call("PUT", `/api/schedules/${s.id}`, { run_now: true }).then((r) => r.conv_id && select(r.conv_id))} className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"><Zap className="size-3.5" /></button>
                <button title={s.enabled ? "Pause" : "Resume"} aria-label={s.enabled ? `Pause ${s.name}` : `Resume ${s.name}`} onClick={() => call("PUT", `/api/schedules/${s.id}`, { enabled: !s.enabled })} className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground">{s.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}</button>
                <button title="Delete" aria-label={`Delete ${s.name}`} onClick={() => { if (confirm(`Delete the scheduled task “${s.name}”?`)) call("DELETE", `/api/schedules/${s.id}`) }} className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-destructive"><Trash2 className="size-3.5" /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      {/* the bottom row: Scheduled (count, next run) · add · the sidebar's fold button */}
      <div className="flex items-center gap-1 px-2 py-1.5">
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-left transition-ui hover:bg-surface-1">
          <AlarmClock data-tone={active.length ? "info" : "muted"} className="tone-text size-3.5 shrink-0" />
          <span className="label-tech text-muted-foreground">Scheduled</span>
          {list.length > 0 && <span className="mono text-meta text-muted-foreground/70">{active.length}{active.length !== list.length ? `/${list.length}` : ""}</span>}
          {!open && soonest && <span className="min-w-0 truncate text-meta text-muted-foreground/70">· {when(soonest.next_run)}</span>}
          <ChevronUp className={cn("ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
        </button>
        <button onClick={() => { setAdding((a) => !a) }} title="Schedule a task" aria-label="Schedule a task" className={iconBtn}><Plus className="size-4" /></button>
        <button onClick={() => useApp.getState().set({ sidebarOpen: false })} title="Hide sidebar (Ctrl+B)" aria-label="Hide sidebar" className={iconBtn}><PanelLeftClose className="size-4" /></button>
      </div>
    </div>
  )
}
