import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowUpRight, Shuffle, X } from "lucide-react"
import { useApp } from "@/lib/store"
import { CLIP_QUOTES, suggestFor } from "@/lib/clip-lines"
import { cn } from "@/lib/utils"

/** Clip: a modern paperclip assistant. It sits at the app window's bottom-right corner, now and then offers a line of
 *  philosophy, and suggests queries (a click puts one in the composer; it never sends).
 *  Shows up once a session a few seconds in, then only when you've been idle for a while (every 3-6 min), never
 *  while you type. Quiet for an hour or hide it from its own bubble; Settings > General brings it back. */

const HIDDEN_KEY = "zen.clip.hidden", SNOOZE_KEY = "zen.clip.snoozeUntil", GREETED_KEY = "zen.clip.greeted"
const ls = {
  get: (k: string) => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k: string, v: string | null) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v) } catch { /* blocked */ } },
}
const pickOne = <T,>(xs: T[]) => xs[(Math.random() * xs.length) | 0]

/** Put text in the composer (not sent), as if typed, and focus it. */
function fillComposer(text: string) {
  const ta = document.querySelector<HTMLTextAreaElement>("main textarea")
  if (!ta) return
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(ta, text)
  ta.dispatchEvent(new Event("input", { bubbles: true }))
  ta.focus()
  ta.setSelectionRange(text.length, text.length)
}

type Say = { quote: string; by: string }

/** The character: a bent-wire paperclip with eyes and eyebrows (SVG; CSS animations in index.css). */
function ClipFigure({ talking }: { talking: boolean }) {
  return (
    <svg viewBox="0 0 44 66" className={cn("clip-bob h-[86px] w-[57px] drop-shadow-[0_6px_14px_rgb(0_0_0/0.45)]", talking && "clip-talking")} aria-hidden="true">
      <defs>
        <linearGradient id="clip-wire" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="oklch(0.95 0.005 85)" />
          <stop offset="0.5" stopColor="oklch(0.78 0.01 265)" />
          <stop offset="1" stopColor="oklch(0.6 0.012 265)" />
        </linearGradient>
      </defs>
      {/* the wire: outer loop, then inner loop, open at the top like a real clip */}
      <path d="M28 18 V52 a8 8 0 0 1 -16 0 V12 a6.5 6.5 0 0 1 13 0 V46 a3 3 0 0 1 -6 0 V20"
        fill="none" stroke="url(#clip-wire)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      {/* eyebrows (raise while talking) */}
      <g className="clip-brows" stroke="oklch(0.85 0.01 85)" strokeWidth="1.6" strokeLinecap="round" fill="none">
        <path d="M7 21 q4 -3 8 -1" /><path d="M24 20 q4 -2 8 1" />
      </g>
      {/* eyes, looking towards the bubble (left) */}
      <g className="clip-eyes">
        <ellipse cx="11" cy="28" rx="4.6" ry="5.4" fill="oklch(0.97 0.004 85)" />
        <ellipse cx="28" cy="28" rx="4.6" ry="5.4" fill="oklch(0.97 0.004 85)" />
        <circle cx="9.6" cy="29" r="2.2" fill="oklch(0.2 0.01 265)" />
        <circle cx="26.6" cy="29" r="2.2" fill="oklch(0.2 0.01 265)" />
        <circle cx="9" cy="28.2" r="0.7" fill="white" /><circle cx="26" cy="28.2" r="0.7" fill="white" />
      </g>
    </svg>
  )
}

export function Clip() {
  const settingsOn = useApp((s) => (s.settings?.ui as any)?.clip)
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const saveSettings = useApp((s) => s.saveSettings)
  const [hidden, setHidden] = useState(() => ls.get(HIDDEN_KEY) === "1")
  const [say, setSay] = useState<Say | null>(null)
  const [tips, setTips] = useState<string[]>([])
  const [shown, setShown] = useState(false)              // the paperclip itself is on screen
  const hold = useRef(false)                              // hovered / focused: don't auto-close
  const lastInput = useRef(Date.now())
  const closeTimer = useRef(0)

  // turned back on in Settings: forget an earlier "hide"
  useEffect(() => { if (settingsOn === true && hidden) { ls.set(HIDDEN_KEY, null); setHidden(false) } }, [settingsOn, hidden])
  const enabled = !hidden && settingsOn !== false

  const speak = useCallback((s: Say) => {
    setSay(s)
    setTips(suggestFor(useApp.getState().current ? useApp.getState().convs[useApp.getState().current!] : null))
    setShown(true)
    window.clearTimeout(closeTimer.current)
    const close = () => { if (hold.current) closeTimer.current = window.setTimeout(close, 4000); else setSay(null) }
    closeTimer.current = window.setTimeout(close, 18000)
  }, [])
  const quip = useCallback(() => speak(pickOne(CLIP_QUOTES)), [speak])

  // once a session, a few seconds after the app is up; then idle-time quotes every 3-6 minutes
  useEffect(() => {
    if (!enabled) { setShown(false); setSay(null); return }
    const snoozed = () => Number(ls.get(SNOOZE_KEY) || 0) > Date.now()
    const onInput = () => { lastInput.current = Date.now() }
    window.addEventListener("keydown", onInput, true)
    window.addEventListener("pointerdown", onInput, true)
    const t0 = window.setTimeout(() => {
      if (snoozed()) return
      setShown(true)
      let greeted = false
      try { greeted = sessionStorage.getItem(GREETED_KEY) === "1"; sessionStorage.setItem(GREETED_KEY, "1") } catch { /* blocked */ }
      if (!greeted) quip()
    }, 6000)
    let next = 0
    const plan = () => { next = window.setTimeout(tick, (180 + Math.random() * 180) * 1000) }
    const tick = () => {
      const idle = Date.now() - lastInput.current > 45000
      const typing = document.activeElement?.tagName === "TEXTAREA" && !!(document.activeElement as HTMLTextAreaElement).value
      if (!document.hidden && idle && !typing && !snoozed()) quip()
      plan()
    }
    plan()
    ;(window as any).__clip = { quip }                   // for tests
    return () => {
      window.clearTimeout(t0); window.clearTimeout(next); window.clearTimeout(closeTimer.current)
      window.removeEventListener("keydown", onInput, true); window.removeEventListener("pointerdown", onInput, true)
    }
  }, [enabled, speak, quip])

  // a different chat: fresh suggestions if the bubble is open
  useEffect(() => { if (say) setTips(suggestFor(conv)) }, [conv?.id])                                // eslint-disable-line

  const snooze = () => { ls.set(SNOOZE_KEY, String(Date.now() + 3600_000)); setSay(null); setShown(false) }
  const hide = () => {
    ls.set(HIDDEN_KEY, "1"); setHidden(true); setSay(null)
    saveSettings({ ui: { clip: false } } as any).catch(() => {})
  }
  const tipList = useMemo(() => tips, [tips])
  if (!enabled || !shown) return null

  return (
    <div className="pointer-events-none fixed bottom-10 right-3 z-[60] flex flex-col items-end"
      onMouseEnter={() => { hold.current = true }} onMouseLeave={() => { hold.current = false }}
      onFocus={() => { hold.current = true }} onBlur={() => { hold.current = false }}>
      {say && (
        <div role="status" aria-live="polite"
          className="clip-bubble pointer-events-auto relative mb-2 mr-6 w-[300px] max-w-[calc(100vw-120px)] rounded-panel border border-line-2 bg-surface-2 p-3 pr-8 shadow-elev-2">
          <button onClick={() => setSay(null)} aria-label="Close" title="Close"
            className="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-md text-muted-foreground transition-ui hover:bg-surface-3 hover:text-foreground">
            <X className="size-3.5" />
          </button>
          <p className="text-body leading-snug text-foreground">“{say.quote}”</p>
          <p className="mt-1 text-caption text-muted-foreground">— {say.by}</p>
          {tipList.length > 0 && (
            <div className="mt-2.5 space-y-1">
              <div className="label-tech text-muted-foreground/60">Try asking</div>
              {tipList.map((t) => (
                <button key={t} onClick={() => { fillComposer(t); setSay(null) }}
                  className="group flex w-full items-start gap-1.5 rounded-chip border border-line-1 bg-surface-1 px-2 py-1.5 text-left text-caption text-foreground/90 transition-ui hover:border-line-3 hover:bg-surface-3">
                  <ArrowUpRight className="mt-0.5 size-3 shrink-0 text-muted-foreground group-hover:text-foreground" />
                  <span>{t}</span>
                </button>
              ))}
            </div>
          )}
          <div className="mt-2.5 flex items-center gap-3 text-meta text-muted-foreground">
            <button onClick={quip} className="flex items-center gap-1 transition-ui hover:text-foreground"><Shuffle className="size-3" /> Another</button>
            <button onClick={snooze} className="transition-ui hover:text-foreground">Quiet for an hour</button>
            <button onClick={hide} className="ml-auto transition-ui hover:text-foreground" title="Bring it back in Settings > General">Hide Clip</button>
          </div>
          {/* the bubble's tail, pointing at Clip */}
          <span aria-hidden="true" className="absolute -bottom-[7px] right-5 size-3 rotate-45 border-b border-r border-line-2 bg-surface-2" />
        </div>
      )}
      <button onClick={() => (say ? setSay(null) : quip())} aria-label={say ? "Close Clip's bubble" : "Ask Clip for a quote"}
        title="Clip" className="clip-enter pointer-events-auto rounded-full p-1 transition-ui hover:bg-surface-2/60">
        <ClipFigure talking={!!say} />
      </button>
    </div>
  )
}
