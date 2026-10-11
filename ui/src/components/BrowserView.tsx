import { useCallback, useEffect, useRef, useState } from "react"
import { AppWindow, ArrowLeft, ArrowRight, Bot, Globe, PanelRightClose, RotateCw, SquareCode } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { showPanel } from "@/lib/dock"
import { useNativeBridge, useNativeSlot } from "@/lib/nativeSlot"

const VIEW_W = 1280
const VIEW_H = 800
const KEYS: Record<string, string> = {
  Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Escape: "Escape", Delete: "Delete", ArrowUp: "ArrowUp",
  ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", PageUp: "PageUp", PageDown: "PageDown",
  Home: "Home", End: "End",
}

async function send(kind: string, body: Record<string, unknown> = {}) {
  const r = await fetch("/api/browser/input", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, ...body }) })
  return r.json().catch(() => ({}))
}

type BrowserState = { url: string; title: string; running?: boolean; mode?: string; agent?: boolean; host?: boolean }

export function BrowserView() {
  const [src, setSrc] = useState<string | null>(null)
  const [agent, setAgent] = useState(false)
  const [state, setState] = useState<BrowserState>({ url: "", title: "" })
  const [url, setUrl] = useState("")
  const [editing, setEditing] = useState(false)
  const seq = useRef("")
  const img = useRef<HTMLImageElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const native = useNativeBridge()
  // inside the app window, the page itself is shown natively; screenshots are only for the Edge fallback
  const nativeShown = !!native && state.mode !== "edge"

  useEffect(() => {
    const load = () => fetch("/api/browser/state").then((r) => r.json()).then((s) => setState(s)).catch(() => {})
    load()
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [])
  useEffect(() => { if (!editing) setUrl(state.url || "") }, [state.url, editing])
  useEffect(() => { if (nativeShown) setAgent(!!state.agent) }, [nativeShown, state.agent])

  // Edge fallback: poll the latest frame (~3 fps), swap only when it changed
  useEffect(() => {
    if (nativeShown) return
    let alive = true
    let last: string | null = null
    const tick = async () => {
      try {
        const r = await fetch("/api/browser/frame", { cache: "no-store" })
        if (r.status === 200) {
          const s = r.headers.get("X-Seq") || ""
          setAgent(r.headers.get("X-Agent") === "1")
          if (s !== seq.current) {
            seq.current = s
            const u = URL.createObjectURL(await r.blob())
            if (alive) setSrc(u)
            if (last) URL.revokeObjectURL(last)
            last = u
          }
        }
      } catch { /* server busy */ }
    }
    tick()
    const t = setInterval(tick, 330)
    return () => { alive = false; clearInterval(t); if (last) URL.revokeObjectURL(last); setSrc(null) }
  }, [nativeShown])

  // popped out: the page lives in its own window (the window tells us when that changes)
  const [popped, setPopped] = useState(false)
  useEffect(() => {
    if (!native?.isPoppedOut) return
    native.isPoppedOut((v) => setPopped(!!v))
    const on = (e: Event) => setPopped(!!(e as CustomEvent).detail)
    window.addEventListener("zen-browser-popout", on)
    return () => window.removeEventListener("zen-browser-popout", on)
  }, [native])

  // native view: tell the window where this panel is (and hide it while it isn't showing)
  const running = !!state.running
  const place = useCallback((x: number, y: number, w: number, h: number, v: boolean) => native?.setRect(x, y, w, h, v), [native])
  useNativeSlot("browser", box, !!native && nativeShown && running && !popped, place)

  const toPage = (e: React.MouseEvent) => {
    const el = img.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: ((e.clientX - r.left) / r.width) * VIEW_W, y: ((e.clientY - r.top) / r.height) * VIEW_H }
  }

  const empty = (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-ui text-muted-foreground">
      <Globe className="size-6 opacity-60" />
      The agent's browser shows here when it browses. You can also type an address above to open a page.
      <span className="text-caption text-muted-foreground/70">Click and type in the page to use it yourself at any time.</span>
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        <Button size="icon-xs" variant="ghost" onClick={() => send("back")} title="Back" aria-label="Back"><ArrowLeft /></Button>
        <Button size="icon-xs" variant="ghost" onClick={() => send("forward")} title="Forward" aria-label="Forward"><ArrowRight /></Button>
        <Button size="icon-xs" variant="ghost" onClick={() => send("reload")} title="Reload" aria-label="Reload"><RotateCw /></Button>
        <form className="min-w-0 flex-1" onSubmit={(e) => { e.preventDefault(); setEditing(false); if (url.trim()) send("navigate", { url: url.trim() }) }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} onFocus={(e) => { setEditing(true); e.target.select() }}
            onBlur={() => setEditing(false)} placeholder="Search or enter an address"
            className="mono w-full rounded-md bg-muted/60 px-2.5 py-1 text-caption outline-none focus:ring-1 focus:ring-ring" />
        </form>
        <span className={cn("flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-meta transition-opacity",
          agent ? "bg-info/15 text-info opacity-100" : "opacity-0")}>
          <Bot className="size-3" /> agent
        </span>
        {nativeShown && native?.openDevTools && (
          <Button size="icon-xs" variant="ghost" onClick={() => { showPanel("devtools"); native.openDevTools?.() }} disabled={!running}
            title="DevTools for this page (F12)" aria-label="Open DevTools"><SquareCode /></Button>
        )}
        {nativeShown && native?.popOut && (
          <Button size="icon-xs" variant="ghost" onClick={() => (popped ? native.popIn?.() : native.popOut?.())}
            title={popped ? "Bring the browser back into the app" : "Pop the browser out into its own window"}
            aria-label={popped ? "Dock browser" : "Pop out browser"}>{popped ? <PanelRightClose /> : <AppWindow />}</Button>
        )}
      </div>
      {nativeShown ? (
        // the window places the real page over this box
        <div ref={box} className="relative min-h-0 flex-1 overflow-hidden bg-black/40">
          {popped ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center text-ui text-muted-foreground">
              <AppWindow className="size-6 opacity-60" />
              The browser is open in its own window.
              <Button size="sm" variant="outline" onClick={() => native?.popIn?.()}><PanelRightClose /> Bring it back</Button>
            </div>
          ) : !running && empty}
        </div>
      ) : (
        <div ref={box} className="relative min-h-0 flex-1 overflow-hidden bg-black/40 outline-none focus:ring-1 focus:ring-info/50" tabIndex={0}
          onKeyDown={(e) => {
            if ((e.target as HTMLElement).tagName === "INPUT") return
            e.preventDefault()
            if (e.ctrlKey || e.metaKey) send("key", { key: `Control+${e.key.length === 1 ? e.key.toLowerCase() : e.key}` })
            else if (KEYS[e.key]) send("key", { key: KEYS[e.key] })
            else if (e.key.length === 1) send("type", { text: e.key })
          }}
          onWheel={(e) => send("scroll", { dy: e.deltaY })}>
          {src ? (
            <img ref={img} src={src} draggable={false}
              onClick={(e) => { box.current?.focus(); const p = toPage(e); if (p) send("click", p) }}
              className="absolute inset-0 m-auto max-h-full max-w-full cursor-pointer select-none object-contain" />
          ) : empty}
        </div>
      )}
      {state.title && <div className="truncate border-t border-border px-3 py-1 text-caption text-muted-foreground">{state.title}</div>}
    </div>
  )
}
