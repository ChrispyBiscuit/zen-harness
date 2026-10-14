import { Volume2, VolumeX } from "lucide-react"
import { ImageCarousel } from "./ImageCarousel"
import { speaker } from "@/lib/tts"
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { ArrowDown, BookOpen, Lightbulb, CheckCircle2, ChevronRight, CircleDot, Loader2, ShieldAlert, XCircle, Compass, FileSpreadsheet, FileText, Image as ImageIcon, Presentation, Paperclip, ScanSearch, ShieldCheck } from "lucide-react"
import { Markdown, SCR_CLOSE, SCR_OPEN } from "./Markdown"
import { ChangesCard, CompactedDivider, TaskDoneCard, ToolCard } from "./cards/ToolCards"
import { useApp } from "@/lib/store"
import { InkRing } from "./Logo"
import type { Attachment, Conv, Msg, TurnStats } from "@/lib/types"
import { fmtBytes } from "@/lib/paths"
import { cn } from "@/lib/utils"

function AttachmentChip({ a }: { a: Attachment }) {
  const openFile = useApp((s) => s.openFile)
  const Icon = a.kind === "image" ? ImageIcon : a.kind === "excel" || a.kind === "table" ? FileSpreadsheet
    : a.kind === "powerpoint" ? Presentation : a.kind === "unknown" ? Paperclip : FileText
  return (
    <button onClick={() => openFile(a.path)}
      className="flex items-center gap-2 rounded-lg border border-line-1 bg-surface-1 px-2 py-1.5 text-left transition-ui hover:bg-surface-2">
      <Icon className="size-4 text-muted-foreground" />
      <span className="max-w-48 truncate text-caption">{a.name}</span>
      <span className="text-meta text-muted-foreground">{fmtBytes(a.size)}</span>
    </button>
  )
}

function UserMessage({ msg }: { msg: Msg }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      {msg.interjection && <span className="text-meta text-muted-foreground">sent while the agent was working</span>}
      {msg.attachments?.length ? (
        <div className="flex max-w-[85%] flex-wrap justify-end gap-1.5">{msg.attachments.map((a) => <AttachmentChip key={a.path} a={a} />)}</div>
      ) : null}
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-panel rounded-br-chip bg-surface-2 px-4 py-2 text-chat">
        {msg.content}
      </div>
      <TurnTime t={msg.t} />
    </div>
  )
}

function TurnTime({ t }: { t?: number }) {
  if (!t) return null // exact times were not recorded for older assistant messages
  const date = new Date(t * 1000)
  return <time dateTime={date.toISOString()} title={date.toLocaleString()}
    className="text-meta text-muted-foreground/80">
    {date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
  </time>
}

function Thinking({ text, live, k }: { text: string; live?: boolean; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const show = !!open                         // collapsed until clicked, also while it is being written
  return (
    <div className="text-ui">
      <button onClick={() => toggle(k)} className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground">
        <Lightbulb className="size-3.5" />
        <span className={cn(live && "shimmer-text")}>{live ? "Thinking" : "Reasoning"}</span>
        <ChevronRight className={cn("size-3.5 transition-transform", show && "rotate-90")} />
      </button>
      {show && (
        <div className="mt-1.5 max-h-72 overflow-auto whitespace-pre-wrap border-l-2 border-border pl-3 text-ui leading-relaxed text-muted-foreground">
          {text}
        </div>
      )}
    </div>
  )
}

/** An answer that failed a final check (fact check, open checklist items, repeat) and was revised: folded away. */
function DraftAnswer({ msg, k }: { msg: Msg; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  return (
    <div className="rounded-card border border-dashed border-line-2">
      <button onClick={() => toggle(k)} className="flex w-full items-center gap-2 min-h-9 px-(--card-px) py-(--card-py) transition-ui hover:bg-surface-2 active:bg-surface-3 rounded-card text-left text-caption text-muted-foreground">
        <FileText className="size-3.5 shrink-0" />
        <span className="truncate">Draft answer — revised after checks{(msg as any).reason ? ` (${(msg as any).reason})` : ""}</span>
        <ChevronRight className={cn("ml-auto size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
      </button>
      {open && <div className="border-t border-line-1 px-4 py-2 text-ui opacity-75"><Markdown text={msg.content} /></div>}
    </div>
  )
}

function FactCheckCard({ msg, k }: { msg: Msg; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const m = msg as any
  const items: any[] = m.items || []
  const bad = items.filter((i) => !i.ok)
  const allOk = bad.length === 0
  return (
    <div className="overflow-hidden rounded-card border border-line-1 bg-surface-1">
      <button onClick={() => toggle(k)} className="flex w-full items-center gap-2 min-h-9 px-(--card-px) py-(--card-py) transition-ui hover:bg-surface-2 active:bg-surface-3 text-left text-caption">
        <ShieldCheck className={cn("size-4 shrink-0", allOk ? "text-success" : "text-warning")} />
        <span className="font-medium">Fact check{m.round > 1 ? " (recheck)" : ""}</span>
        <span className="text-muted-foreground">
          {m.total ? `${m.total} critical fact${m.total === 1 ? "" : "s"} · ${m.ok} verified${bad.length ? ` · ${bad.length} not supported${m.round === 1 ? " — sent back to fix" : ""}` : ""}` : "no critical facts found"}
        </span>
        {items.length > 0 && <ChevronRight className={cn("ml-auto size-3.5 text-muted-foreground transition-transform", open && "rotate-90")} />}
      </button>
      {open && items.length > 0 && (
        <div className="divide-y divide-border/50 border-t border-border/60">
          {items.map((it, i) => (
            <div key={i} className="flex items-start gap-2 px-(--card-px) py-1.5 text-caption">
              <span className={cn("mt-0.5 shrink-0", it.ok ? "text-success" : "text-warning")}>{it.ok ? "✓" : "✗"}</span>
              <span className="min-w-0 flex-1">
                <span className="text-foreground/90">{it.claim}</span>
                <span className="block truncate text-meta text-muted-foreground">
                  {it.ok ? `found in ${it.matched || it.source}` : it.why}{it.key ? ` · “${it.key}”` : ""}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** The first step of a turn: how the request was understood (goal, conditions, ambiguity, risk). Collapsed to one
 *  line; the full reading opens on click. An "ask first" or irreversible flag shows in the header. */
function InterpretationCard({ msg, k }: { msg: Msg; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const m = msg as any
  const list = (xs?: string[]) => (xs || []).filter(Boolean)
  const rows: [string, string][] = [
    ["Goal", msg.content + (m.deliverable ? ` · ${m.deliverable}` : "")],
    ...(list(m.constraints).length ? [["Your constraints", list(m.constraints).join(" · ")] as [string, string]] : []),
    ...(list(m.conditions).length ? [["Must satisfy", list(m.conditions).join(" · ")] as [string, string]] : []),
    ...(m.ambiguity ? [["Ambiguity", m.ambiguity + (m.ask && m.question ? ` → asks: ${m.question}` : m.reading ? ` → reading: ${m.reading}` : "")] as [string, string]] : []),
    ...(list(m.implicit).length ? [["Also likely wanted", list(m.implicit).join(" · ")] as [string, string]] : []),
  ]
  return (
    <div className="overflow-hidden rounded-card border border-line-1 bg-surface-1">
      <button onClick={() => toggle(k)} className="flex w-full items-center gap-2 min-h-9 px-(--card-px) py-(--card-py) transition-ui hover:bg-surface-2 active:bg-surface-3 text-left text-ui">
        <ScanSearch className="size-4 shrink-0 text-info" />
        <span className="shrink-0 font-medium">Understood as</span>
        {!open && <span className="min-w-0 flex-1 truncate text-caption text-muted-foreground">{msg.content}</span>}
        {m.ask && <span data-tone="warning" className="tone-soft tone-text shrink-0 rounded-full px-2 py-0.5 label-tech">asks first</span>}
        {m.irreversible && <span data-tone="destructive" className="tone-soft tone-text shrink-0 rounded-full px-2 py-0.5 label-tech">irreversible</span>}
        <ChevronRight className={cn("ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-border/60 px-4 py-3 text-ui">
          {rows.map(([label, v]) => (
            <div key={label} className="flex gap-3">
              <span className="w-32 shrink-0 label-tech pt-0.5 text-muted-foreground">{label}</span>
              <span className="min-w-0">{v}</span>
            </div>
          ))}
          {m.seconds != null && <div className="pt-1 label-tech text-muted-foreground/60">{m.seconds}s</div>}
        </div>
      )}
    </div>
  )
}

function PlanCard({ msg, k }: { msg: Msg; k: string }) {
  const open = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const show = !!open                                // collapsed until clicked, also while being written
  const tail = msg.streaming ? (msg.content || "").trim().split("\n").filter(Boolean).pop() || "" : ""
  return (
    <div className="overflow-hidden rounded-card border border-line-1 bg-surface-1">
      <button onClick={() => toggle(k)} className="flex w-full items-center gap-2 min-h-9 px-(--card-px) py-(--card-py) transition-ui hover:bg-surface-2 active:bg-surface-3 text-left text-ui">
        <Compass className={cn("size-4 text-info", msg.streaming && "animate-pulse")} />
        <span className={cn("font-medium", msg.streaming && "shimmer-text")}>{msg.streaming ? "Planning the approach" : "Approach"}</span>
        <span className="text-caption text-muted-foreground">
          {msg.streaming ? `up to ${((msg.budget || 0) / 1000).toFixed(0)}k tokens` : `${msg.tokens ?? "?"} tokens · ${Math.round(msg.seconds || 0)}s`}
        </span>
        {tail && !show && <span className="min-w-0 flex-1 truncate text-caption text-muted-foreground/70">{tail}</span>}
        <ChevronRight className={cn("ml-auto size-3.5 text-muted-foreground transition-transform", show && "rotate-90")} />
      </button>
      {show && (
        <div className="max-h-[480px] overflow-y-auto border-t border-border/60 px-4 py-3 text-ui">
          <Markdown text={msg.content} />
        </div>
      )}
    </div>
  )
}

function Stats({ s }: { s: TurnStats }) {
  const parts = [`${s.steps} step${s.steps === 1 ? "" : "s"}`, `${s.tool_calls} tool call${s.tool_calls === 1 ? "" : "s"}`]
  if (s.context_used) parts.push(`${(s.context_used / 1000).toFixed(1)}k / ${((s.context_max || 0) / 1000).toFixed(0)}k context`)
  parts.push(`${Math.round(s.seconds)}s`)
  if ((s as any).exec_steps) parts.push(`executor ${(s as any).exec_steps} / planner ${(s as any).planner_steps || 0} steps`)
  const sp = (s as any).speech
  if (sp) parts.push(`first word ${sp.first_word}s (tools ${sp.decide}s + ${sp.tools}s, plan ${sp.plan}s) · ${sp.model}`)
  if (s.compactions) parts.push(`compressed ×${s.compactions}`)
  if (s.failed_calls) parts.push(`${s.failed_calls} failed`)
  if (s.advisor_cost) parts.push(`advisor $${s.advisor_cost.toFixed(3)}`)
  return <div className="mt-2 text-caption text-muted-foreground/70">{parts.join(" · ")}</div>
}

// glyphs for the decode effect: no markdown syntax characters, so the scramble never turns into formatting
const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghkmnpqrstuvwxyz0123456789@%&?ΔΘΛΞΣΦΨΩλμπψ¥§¤░▒▓"
const SCRAMBLE_LEN = 8
function scramble(s: string) {
  let out = ""
  for (const ch of s) out += /\s/.test(ch) ? ch : GLYPHS[(Math.random() * GLYPHS.length) | 0]
  return out
}

const REDUCED_MOTION = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches

/** Reveal streamed text a few characters per frame. The step grows with the backlog, so the display never
 *  falls far behind the model; text that was already complete when it mounted shows at once. */
function useTypewriter(text: string, live: boolean) {
  const animate = useRef(live && !REDUCED_MOTION)          // decided once: old messages never animate
  const [n, setN] = useState(animate.current ? 0 : text.length)
  const [, setFrame] = useState(0)                           // re-roll the scramble glyphs every tick
  const liveRef = useRef(live)
  liveRef.current = live
  const nRef = useRef(n)
  nRef.current = n
  const target = useRef(text)
  target.current = text
  useEffect(() => {
    if (!animate.current) return
    // Time-based: one tick per screen frame (~16 ms). Slow streams type one character at a time (≥ 45 chars/s);
    // fast ones reveal several per frame so the display never trails the model by more than ~0.35 s — a 60 Hz
    // screen can't show more than 60 single characters a second, and models stream 400-800.
    // Timers, not animation frames: frames stop in a hidden window.
    const MIN_RATE = 45, MAX_LAG = 0.35
    let timer = 0
    let stopped = false
    let last = performance.now()
    let carry = 0                                            // fractional characters owed
    const step = () => {
      if (stopped) return
      const now = performance.now()
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const total = target.current.length
      const cur = nRef.current
      if (document.hidden) {                                 // nobody is watching: just show everything
        if (cur < total) { nRef.current = total; setN(total) }
      } else if (cur < total) {
        const backlog = total - cur
        carry += Math.max(MIN_RATE, backlog / MAX_LAG) * dt
        const add = Math.min(backlog, Math.max(1, Math.floor(carry)))
        carry = Math.max(0, carry - add)
        nRef.current = cur + add
        setN(cur + add)
        setFrame((f) => (f + 1) % 1_000_000)                 // re-roll the scramble glyphs
      } else if (!liveRef.current) {
        return                                               // finished and caught up: stop
      } else {
        carry = 0
      }
      timer = window.setTimeout(step, target.current.length > nRef.current ? 16 : 40)
    }
    timer = window.setTimeout(step, 16)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [])
  if (!animate.current) return { shown: text, typing: false, tail: "" }
  let shown = text.slice(0, n)
  if (n < text.length) {                                     // mid-typing: don't show half a formula
    const marks = [...shown.matchAll(/(?<![\\$])\$(?!\d)/g)]  // $ that open/close math (not $25, not \$)
    if (marks.length % 2) shown = shown.slice(0, marks[marks.length - 1].index)
  }
  const typing = live || n < text.length
  // the next few characters show as cycling glyphs and lock into place as the cursor reaches them; while the
  // model is still producing and the display has caught up, a few glyphs keep flickering at the edge
  let tail = ""
  if (typing && shown.length === n) {
    const ahead = text.slice(n, n + SCRAMBLE_LEN).split("\n")[0]
    tail = scramble(ahead)                                   // only real, not-yet-shown characters
  }
  return { shown, typing, tail }
}

function SpeakButton({ k, text }: { k: string; text: string }) {
  const speaking = useApp((s) => (s as any).speaking === k)
  return (
    <button onClick={() => (speaking ? speaker.stop() : speaker.feed(k, text, true))} title={speaking ? "Stop reading" : "Read aloud"}
      className={cn("rounded p-1 text-muted-foreground/70 hover:bg-surface-2 hover:text-foreground", speaking && "text-info")}>
      {speaking ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}
    </button>
  )
}

/** An assistant message. "answer": the reply (markdown, images, read-aloud). "narration": a step's running commentary
 *  inside a folded run (quiet small text: no images, read-aloud button or stats). */
const AssistantMessage = memo(function AssistantMessage({ msg, k, variant = "answer", hideStats }: {
  msg: Msg; k: string; variant?: "answer" | "narration"; hideStats?: boolean
}) {
  const narration = variant === "narration"
  const { shown, typing, tail } = useTypewriter(msg.content || "", !!msg.streaming)
  // read aloud as the reply arrives (only replies that were live when they appeared, never old ones)
  const autoRead = useApp((s) => !!(s.settings?.ui as any)?.tts_auto || (s.current ? (s.convs[s.current] as any)?.mode === "speech" : false))
  const wasLive = useRef(!!msg.streaming)
  useEffect(() => {
    if (!autoRead || !wasLive.current || !msg.content || msg.error) return
    speaker.feed(k, msg.content, !msg.streaming)
  }, [autoRead, msg.content, msg.streaming])
  return (
    <div className={cn("space-y-2", narration && "narration space-y-1")}>
      {msg.thinking ? <Thinking text={msg.thinking} live={msg.streaming && !msg.content} k={`${k}:think`} /> : null}
      {!narration && msg.images?.length && (msg.content || !msg.streaming) ? <ImageCarousel images={msg.images} /> : null}
      {msg.content ? (
        <div className={cn(msg.error && "text-destructive", typing && "typing")}>
          <Markdown text={tail ? shown + SCR_OPEN + tail + SCR_CLOSE : shown} />
        </div>
      ) : null}
      {!narration && msg.content && !msg.streaming ? <div className="-mt-1 flex"><SpeakButton k={k} text={msg.content} /></div> : null}
      <TurnTime t={msg.t} />
      {!narration && !hideStats && msg.stats && !msg.streaming && (msg.stats.tool_calls || msg.stats.steps > 1) ? <Stats s={msg.stats} /> : null}
    </div>
  )
}, (a, b) => a.msg === b.msg && a.variant === b.variant && a.hideStats === b.hideStats)

function Item({ msg, conv, index, variant, hideStats }: {
  msg: Msg; conv: Conv; index: number; variant?: "answer" | "narration"; hideStats?: boolean
}) {
  const k = `${conv.id}:${index}`
  if (msg.role === "user") return <UserMessage msg={msg} />
  switch (msg.kind) {
    case "tool":
      return <ToolCard msg={msg} convId={conv.id} index={index} />
    case "changes":
      return <ChangesCard msg={msg} convId={conv.id} index={index} />
    case "compacted":
      return <CompactedDivider msg={msg} k={k} />
    case "task_done":
      return <TaskDoneCard msg={msg} />
    case "plan":
      return <PlanCard msg={msg} k={k} />
    case "interpretation":
      return <InterpretationCard msg={msg} k={k} />
    case "factcheck":
      return <FactCheckCard msg={msg} k={k} />
    case "draft":
      return <DraftAnswer msg={msg} k={k} />
    case "playbook":
      return (
        <button onClick={() => useApp.getState().set({ settingsOpen: true, settingsTab: "memory" } as any)}
          className="flex w-full items-center gap-2 rounded-card border border-line-1 bg-surface-1 min-h-9 px-(--card-px) py-(--card-py) transition-ui hover:bg-surface-2 active:bg-surface-3 text-left text-caption">
          <BookOpen className="size-4 shrink-0 text-info" />
          <span className="truncate"><span className="text-muted-foreground">Playbook:</span> {msg.content}
            {(msg as any).also?.length ? <span className="text-muted-foreground"> + {(msg as any).also.join(", ")}</span> : null}</span>
          {(msg as any).notes > 0 && <span className="ml-auto shrink-0 text-meta text-muted-foreground">{(msg as any).notes} lesson{(msg as any).notes === 1 ? "" : "s"} from earlier runs</span>}
        </button>
      )
    case undefined:
    case null:
    case "":
      return <AssistantMessage msg={msg} k={k} variant={variant} hideStats={hideStats} />
    default:
      return null                                  // swarm-mode messages: shown in the Qt window
  }
}

/* ------------------------------------------------------------------ turns: one run per question */

type Entry = { msg: Msg; index: number }
type Turn = { user?: Entry; work: Entry[]; answer?: Entry; note?: Entry; after: Entry[] }

const isPlain = (m: Msg) => m.role === "assistant" && !m.kind
// the fact check's closing note arrives as its own message after the answer (with the run's stats)
const NOT_VERIFIED = /^\s*\*?not verified against a source in this chat:?\*?/i
const isNote = (m: Msg) => isPlain(m) && NOT_VERIFIED.test(m.content || "")

/** Split the chat into turns: a question, the work done for it, the answer, and what follows the answer (the
 *  files-changed card, the last fact check, delegated results). Messages typed while the agent works stay inside the
 *  turn. The answer is the turn's last plain assistant message with text; a live turn has no answer yet. */
function toTurns(msgs: Msg[], liveLast: boolean): Turn[] {
  const turns: Turn[] = []
  let cur: Turn = { work: [], after: [] }
  let items: Entry[] = []
  const close = (live: boolean) => {
    let ans = -1
    if (!live) for (let j = items.length - 1; j >= 0; j--) {
      const m = items[j].msg
      if (isPlain(m) && (m.content || m.error) && !isNote(m)) { ans = j; break }
    }
    if (ans >= 0) {
      cur.answer = items[ans]
      const note = [...items.slice(ans + 1)].reverse().find((e) => isNote(e.msg))
      if (note) cur.note = note
      const tail = items.slice(ans + 1), before = items.slice(0, ans)
      const lastCheck = [...tail].reverse().find((e) => e.msg.kind === "factcheck")
      for (const e of tail) {
        if (e === cur.note) continue
        if (e.msg.kind === "changes" || e.msg.kind === "task_done" || e === lastCheck) cur.after.push(e)
        else before.push(e)
      }
      cur.work = before
    } else cur.work = items
    if (cur.user || items.length) turns.push(cur)
  }
  msgs.forEach((m, index) => {
    if (m.role === "user" && !m.interjection) {
      close(false)
      cur = { user: { msg: m, index }, work: [], after: [] }
      items = []
    } else items.push({ msg: m, index })
  })
  close(liveLast)
  return turns
}

function fmtDuration(sec: number) {
  const s = Math.round(sec)
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

/** The fact check's "not verified" note, under the answer: a quiet amber line, not another post. */
function NotVerified({ msg }: { msg: Msg }) {
  const text = (msg.content || "").replace(NOT_VERIFIED, "").replace(/[*_]/g, "").trim()
  return (
    <div data-tone="warning" className="tone-wash tone-edge flex items-start gap-2 rounded-card border px-(--card-px) py-1.5 text-caption">
      <ShieldAlert className="tone-text mt-0.5 size-3.5 shrink-0" />
      <span><span className="tone-text font-medium">Not verified against a source:</span> <span className="text-foreground/85">{text}</span></span>
    </div>
  )
}

/** The one line that stands for a turn's work; click to see the steps. Open while the run is live, folded after. */
/** `resting`: the ensō under the last output is showing (nothing arriving) — the run line holds still then, so
 *  only one thing on screen says "working". */
function RunBlock({ turn, conv, live, resting }: { turn: Turn; conv: Conv; live: boolean; resting?: boolean }) {
  const k = `${conv.id}:run:${turn.user?.index ?? turn.work[0]?.index ?? 0}`
  const toggled = useApp((s) => s.expanded[k])
  const toggle = useApp((s) => s.toggle)
  const status = useApp((s) => s.status[conv.id])
  const open = live ? !toggled : !!toggled                  // live: open unless closed; done: closed unless opened
  const work = turn.work
  const tools = work.filter((e) => e.msg.kind === "tool")
  const failed = tools.filter((e) => e.msg.tool?.ok === false).length
  const drafts = work.filter((e) => e.msg.kind === "draft").length
  const st = turn.answer?.msg.stats ?? turn.note?.msg.stats
  const steps = st?.steps ?? work.filter((e) => isPlain(e.msg)).length
  const calls = st?.tool_calls ?? tools.length
  const err = !!turn.answer?.msg.error || turn.answer?.msg.content?.trim() === "(stopped)"
  const tone = live ? "info" : err ? "destructive" : failed ? "warning" : "success"
  const Icon = live ? (resting ? CircleDot : Loader2) : err ? XCircle : CheckCircle2
  const parts: string[] = []
  if (live) parts.push(status || "Working")
  else parts.push(st?.seconds != null ? `Worked ${fmtDuration(st.seconds)}` : calls ? "Worked" : "Thought it through")
  if (steps) parts.push(`${steps} step${steps === 1 ? "" : "s"}`)
  if (calls) parts.push(`${calls} tool${calls === 1 ? "" : "s"}`)
  const tip = [parts.join(" · "), st?.context_used ? `${(st.context_used / 1000).toFixed(1)}k of ${((st.context_max || 0) / 1000).toFixed(0)}k context` : "",
    failed ? `${failed} step${failed === 1 ? "" : "s"} failed${err ? "" : " (the agent recovered)"}` : "", drafts ? `${drafts} draft answer${drafts === 1 ? "" : "s"} revised after checks` : ""]
    .filter(Boolean).join("\n")
  return (
    <div className="rounded-card">
      <button onClick={() => toggle(k)} aria-expanded={open} title={tip}
        className="group flex w-full items-center gap-2 rounded-card px-2 py-1.5 text-left text-caption text-muted-foreground transition-ui hover:bg-surface-1 hover:text-foreground">
        <Icon data-tone={tone} className={cn("tone-text size-3.5 shrink-0", live && !resting && "animate-spin")} />
        <span className={cn("min-w-0 truncate", live && !resting && "shimmer-text")}>{parts.join(" · ")}</span>
        {failed > 0 && <span data-tone={err ? "destructive" : "warning"} className="tone-soft tone-text shrink-0 rounded-full px-1.5 text-meta">{failed} failed</span>}
        {drafts > 0 && <span className="shrink-0 rounded-full bg-surface-2 px-1.5 text-meta">{drafts} draft{drafts === 1 ? "" : "s"} revised</span>}
        <ChevronRight className={cn("ml-auto size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="enter-fade ml-3.5 mt-1 flex flex-col gap-(--gap-item) border-l border-line-2 pb-1 pl-4">
          {work.map((e) => <Item key={e.index} msg={e.msg} conv={conv} index={e.index} variant="narration" />)}
        </div>
      )}
    </div>
  )
}

/** Under the last output while the model works but nothing is arriving (reading a long prompt, thinking it doesn't
 *  stream, a slow tool): without it a quiet run looks hung. The status line (e.g. "12,000 / 40,000 tokens read")
 *  goes beside it when there is one. */
function Meditating({ status }: { status?: string }) {
  const detail = status && /\d/.test(status) ? status : ""          // only a specific one ("12,000 / 40,000 tokens read")
  return (
    <div role="status" aria-live="polite" className="enter-fade flex items-center gap-2 text-ui text-muted-foreground">
      <InkRing />
      <span className="shimmer-text">Meditating</span>
      {detail && <span className="min-w-0 truncate text-caption">· {detail}</span>}
    </div>
  )
}

const QUIET_MS = 1200                 // no new output for this long while running -> show the ensō

export function Transcript({ conv }: { conv: Conv }) {
  const ref = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  // Follow the stream (keep the newest text in view) until the user scrolls themselves. Only the user's own input
  // (wheel, touch, keys, the scrollbar) changes this: scrolling up stops following at once, before the next chunk
  // can pull them back; scrolling all the way down again (or "Latest") resumes it. Our own scrolls never count.
  const follow = useRef(true)
  const lastUserInput = useRef(0)
  const [showJump, setShowJump] = useState(false)
  const status = useApp((s) => s.status[conv.id])
  const running = useApp((s) => s.threads.find((t) => t.id === conv.id)?.running)

  const toBottom = () => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight }
  const userInput = () => { lastUserInput.current = performance.now() }
  const stopFollowing = () => { follow.current = false; setShowJump(true) }
  useLayoutEffect(() => { if (follow.current) toBottom() })
  // the reply types itself out between store updates: keep the bottom in view while it grows
  useEffect(() => {
    const el = content.current
    if (!el) return
    const ro = new ResizeObserver(() => { if (follow.current) toBottom() })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    follow.current = true
    setShowJump(false)
    toBottom()
  }, [conv.id])

  const msgs = conv.messages
  const turns = useMemo(() => toTurns(msgs, !!running), [msgs, running])
  // every streamed chunk (text, thinking, a tool call being written, a result) replaces the messages array, so a
  // quiet spell is simply "the array hasn't changed for QUIET_MS"
  const [quiet, setQuiet] = useState(false)
  useEffect(() => {
    setQuiet(false)
    if (!running) return
    const t = window.setTimeout(() => setQuiet(true), QUIET_MS)
    return () => window.clearTimeout(t)
  }, [msgs, running])
  return (
    <div className="relative min-h-0 flex-1">
      <div ref={ref} className="h-full overflow-y-auto"
        onWheel={(e) => { userInput(); if (e.deltaY < 0) stopFollowing() }}
        onTouchMove={() => { userInput(); stopFollowing() }}
        onKeyDown={(e) => {
          if (["ArrowUp", "PageUp", "Home"].includes(e.key)) { userInput(); stopFollowing() }
          else if (["ArrowDown", "PageDown", "End", " "].includes(e.key)) userInput()
        }}
        onPointerDown={(e) => { if (e.target === e.currentTarget) userInput() }}   // the scrollbar itself
        onScroll={(e) => {
          const el = e.currentTarget
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8
          if (performance.now() - lastUserInput.current < 600) {     // the user scrolled (not us, not the stream)
            follow.current = atBottom
            setShowJump(!atBottom)
          } else if (!follow.current) setShowJump(!atBottom)
        }}>
        <div ref={content} data-density-scope className="mx-auto flex max-w-3xl flex-col gap-(--gap-item) px-6 pb-10 pt-6">
          {turns.map((t, ti) => {
            const live = !!running && ti === turns.length - 1
            const showRun = t.work.length > 0
            const lean = !live && t.work.every((e) => isPlain(e.msg) && !e.msg.content)   // only reasoning: no run line
            return (
              <div key={t.user?.index ?? `t${ti}`} className={cn("flex flex-col gap-(--gap-item)", ti > 0 && "mt-4")}>
                {t.user && <Item msg={t.user.msg} conv={conv} index={t.user.index} />}
                {showRun && !lean && <RunBlock turn={t} conv={conv} live={live} resting={live && quiet} />}
                {lean && t.work.map((e) => <Item key={e.index} msg={e.msg} conv={conv} index={e.index} />)}
                {t.answer && <Item msg={t.answer.msg} conv={conv} index={t.answer.index} hideStats={showRun && !lean} />}
                {t.note && <NotVerified msg={t.note.msg} />}
                {t.after.map((e) => <Item key={e.index} msg={e.msg} conv={conv} index={e.index} />)}
              </div>
            )
          })}
          {running && quiet && <Meditating status={status} />}
        </div>
      </div>
      {showJump && (
        <button onClick={() => { follow.current = true; setShowJump(false); toBottom() }}
          className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line-2 bg-surface-2 px-3 py-1 text-caption shadow-elev-2 transition-ui hover:bg-surface-3">
          <ArrowDown className="size-3.5" /> Latest
        </button>
      )}
    </div>
  )
}
