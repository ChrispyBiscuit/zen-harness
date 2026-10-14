import { AlertTriangle, CheckCircle2, ChevronRight, CircleDashed, CircleDot, MinusCircle } from "lucide-react"
import type { Display, Msg } from "@/lib/types"
import { cn } from "@/lib/utils"
import { MeterBar } from "./MeterBar"

export type Sub = { step: string; status: string }
export type Milestone = { title: string; status: string; subtasks?: Sub[] }
export type Checklist = { goal?: string; milestones?: Milestone[]; steps?: Sub[]; notes?: string[]; next?: string; path?: string; [k: string]: any }

export function StepIcon({ status, className }: { status: string; className?: string }) {
  const c = cn("size-4 shrink-0", className)
  if (status === "done") return <CheckCircle2 data-tone="success" className={cn(c, "tone-text")} />
  if (status === "in_progress") return <CircleDot data-tone="info" className={cn(c, "tone-text animate-pulse")} />
  if (status === "blocked") return <AlertTriangle data-tone="warning" className={cn(c, "tone-text")} />
  if (status === "skipped") return <MinusCircle data-tone="muted" className={cn(c, "tone-text")} />
  return <CircleDashed className={cn(c, "text-muted-foreground/60")} />
}

/** Old flat trackers (steps) show as one milestone. */
export function milestonesOf(t: Checklist): Milestone[] {
  if (t.milestones) return t.milestones
  return t.steps?.length ? [{ title: "Steps", status: "pending", subtasks: t.steps }] : []
}

export function progress(t: Checklist) {
  let done = 0, total = 0
  for (const m of milestonesOf(t)) {
    const units = m.subtasks?.length ? m.subtasks : [{ step: m.title, status: m.status }]
    total += units.length
    done += units.filter((u) => u.status === "done" || u.status === "skipped").length
  }
  return { done, total, pct: total ? Math.round((100 * done) / total) : 0 }
}

export function current(t: Checklist): { id: string; text: string } | null {
  const ms = milestonesOf(t)
  for (const want of ["in_progress", "pending", "blocked"]) {
    for (let i = 0; i < ms.length; i++) {
      const subs = ms[i].subtasks || []
      if (!subs.length && ms[i].status === want) return { id: `${i + 1}`, text: ms[i].title }
      for (let j = 0; j < subs.length; j++) if (subs[j].status === want) return { id: `${i + 1}.${j + 1}`, text: subs[j].step }
    }
  }
  return null
}

/** The most recent written approach (planning phase) in a conversation. */
export function latestPlan(msgs: Msg[]): Msg | null {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].kind === "plan") return msgs[i]
  return null
}

/** The task checklist, excluding module verification cards that share the tracker display. */
export function latestChecklist(msgs: Msg[]): Display | null {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const d = msgs[i].tool?.display
    if (msgs[i].kind === "tool" && msgs[i].tool?.name === "update_tracker" && d?.type === "tracker") return d
  }
  return null
}

export function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  return (
    <MeterBar pct={pct} tone="success" label="Checklist progress" className={cn("h-1", className)} />
  )
}

export function ChecklistView({ t, compact }: { t: Checklist; compact?: boolean }) {
  const ms = milestonesOf(t)
  const cur = current(t)
  return (
    <div className="space-y-2.5">
      {ms.map((m, i) => {
        const subs = m.subtasks || []
        const d = subs.filter((s) => s.status === "done" || s.status === "skipped").length
        const finished = m.status === "done" || m.status === "skipped"
        return (
          <div key={i}>
            <div className="flex items-center gap-2">
              <StepIcon status={m.status} />
              <span className={cn("text-ui font-medium", finished && "text-muted-foreground")}>
                <span className="mr-1 text-muted-foreground/70">{i + 1}.</span>{m.title}
              </span>
              {subs.length > 0 && <span className="ml-auto text-meta text-muted-foreground">{d}/{subs.length}</span>}
            </div>
            {subs.length > 0 && !(compact && finished) && (
              <ul className="ml-[7px] mt-1 space-y-1 border-l border-border pl-4">
                {subs.map((s, j) => {
                  const id = `${i + 1}.${j + 1}`
                  const isCur = cur?.id === id
                  return (
                    <li key={j} className={cn("flex items-start gap-2 text-caption", isCur && "font-medium text-foreground")}>
                      <StepIcon status={s.status} className="mt-0.5 size-3.5" />
                      <span className={cn(s.status === "done" || s.status === "skipped"
                        ? "text-muted-foreground line-through decoration-muted-foreground/40" : "text-foreground/85")}>
                        {s.step}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )
      })}
      {t.next && (
        <div className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-1.5 text-caption">
          <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-info" />
          <span><span className="text-muted-foreground">Next: </span>{t.next}</span>
        </div>
      )}
    </div>
  )
}
