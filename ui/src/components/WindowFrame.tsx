import { useEffect, useRef, useState } from "react"
import { Copy, Minus, Square, X } from "lucide-react"
import { LogoMark } from "./Logo"
import { cn } from "@/lib/utils"

/** The desktop window's chrome, drawn by the app in the Zazen theme. app_web.py opens the page in a frameless Qt
 *  window and exposes `window.zenWindow` over QWebChannel; moving and resizing are handed to the OS, so Windows
 *  snap and Win+arrow keys still work. There is no separate title bar: the app's own top bar (and the sidebar's
 *  logo row) are the drag area, with WindowControls at the right end. In a normal browser all of this renders
 *  nothing (`?frame=1` shows it with a stand-in bridge, for development). */
type Bridge = {
  startMove(): void; startResize(edges: string): void; minimize(): void; toggleMaximize(): void; close(): void
  state(cb: (s: string) => void): void
  stateChanged: { connect(cb: (s: string) => void): void }
}

const DEV_FRAME = typeof location !== "undefined" && new URLSearchParams(location.search).get("frame") === "1"
const IN_QT = typeof navigator !== "undefined" && /QtWebEngine/.test(navigator.userAgent)
const noop = () => {}
const STAND_IN: Bridge = { startMove: noop, startResize: noop, minimize: noop, toggleMaximize: noop, close: noop,
  state: (cb) => cb("normal"), stateChanged: { connect: noop } }

/** The window bridge once QWebChannel is ready (null in a normal browser). */
export function useWindowBridge() {
  const [bridge, setBridge] = useState<Bridge | null>(() => (window as any).zenWindow ?? (DEV_FRAME && !IN_QT ? STAND_IN : null))
  useEffect(() => {
    if (bridge) return
    const on = () => setBridge((window as any).zenWindow ?? null)
    window.addEventListener("zen-window-ready", on)
    on()
    return () => window.removeEventListener("zen-window-ready", on)
  }, [bridge])
  return bridge
}

/** "normal" | "maximized" | "minimized", kept up to date. */
function useWindowState(bridge: Bridge | null) {
  const [state, setState] = useState("normal")
  useEffect(() => {
    if (!bridge) return
    bridge.state((s) => setState(s))
    bridge.stateChanged.connect((s) => setState(s))
  }, [bridge])
  return state
}

// anything you can click, type in or drag inside a drag area keeps its own behaviour
const INTERACTIVE = 'button, a, input, select, textarea, label, summary, [role="button"], [role="menuitem"], ' +
  '[role="tab"], [role="slider"], [role="switch"], [contenteditable="true"], [data-no-drag]'

/** A pointer-down handler that turns empty space into the window's title bar: drag moves the window (with snap),
 *  a quick second press toggles maximise. Returns undefined outside the desktop window. */
export function useWindowDrag() {
  const bridge = useWindowBridge()
  const lastDown = useRef(0)
  if (!bridge) return undefined
  return (e: React.PointerEvent) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest(INTERACTIVE)) return
    const now = performance.now()
    if (now - lastDown.current < 350) { lastDown.current = 0; bridge.toggleMaximize(); return }
    lastDown.current = now
    bridge.startMove()
  }
}

/** Minimise / maximise-restore / close, full height of the bar they sit in. */
export function WindowControls({ className }: { className?: string }) {
  const bridge = useWindowBridge()
  const state = useWindowState(bridge)
  if (!bridge) return null
  const maximized = state === "maximized"
  const btn = "grid h-full w-11 place-items-center text-muted-foreground transition-ui hover:bg-surface-2 hover:text-foreground focus-visible:-outline-offset-2"
  return (
    <div data-no-drag className={cn("flex h-full shrink-0 items-stretch border-l border-line-1", className)}>
      <button className={btn} aria-label="Minimize" title="Minimize" onClick={() => bridge.minimize()}>
        <Minus className="size-3.5" strokeWidth={1.75} />
      </button>
      <button className={btn} aria-label={maximized ? "Restore" : "Maximize"} title={maximized ? "Restore" : "Maximize"}
        onClick={() => bridge.toggleMaximize()}>
        {maximized ? <Copy className="size-3 -scale-x-100" strokeWidth={1.75} /> : <Square className="size-3" strokeWidth={1.75} />}
      </button>
      <button className={cn(btn, "hover:bg-destructive hover:text-white")} aria-label="Close" title="Close" onClick={() => bridge.close()}>
        <X className="size-4" strokeWidth={1.75} />
      </button>
    </div>
  )
}

const EDGES: [string, string][] = [
  ["top", "top-0 inset-x-2 h-1 cursor-ns-resize"], ["bottom", "bottom-0 inset-x-2 h-1 cursor-ns-resize"],
  ["left", "left-0 inset-y-2 w-1 cursor-ew-resize"], ["right", "right-0 inset-y-2 w-1 cursor-ew-resize"],
  ["top,left", "top-0 left-0 size-2 cursor-nwse-resize"], ["top,right", "top-0 right-0 size-2 cursor-nesw-resize"],
  ["bottom,left", "bottom-0 left-0 size-2 cursor-nesw-resize"], ["bottom,right", "bottom-0 right-0 size-2 cursor-nwse-resize"],
]

/** Invisible resize handles along the window's edges (not while maximised). */
export function ResizeEdges() {
  const bridge = useWindowBridge()
  const state = useWindowState(bridge)
  if (!bridge || state === "maximized") return null
  return <>{EDGES.map(([edge, cls]) => (
    <div key={edge} aria-hidden="true" className={cn("fixed z-[2000]", cls)}
      onPointerDown={(e) => { if (e.button === 0) { e.preventDefault(); bridge.startResize(edge) } }} />
  ))}</>
}

/** The window's title bar, full width across the top: the ensō and name at the left, then whatever the app puts
 *  in it (the chat's title, its buttons), and the window controls at the right end. Empty space drags the window.
 *  The same bar from the "Starting…" screen to the loaded app, so nothing jumps when the app arrives. */
export function TitleBar({ children }: { children?: React.ReactNode }) {
  const drag = useWindowDrag()
  return (
    <div onPointerDown={drag} role="banner" aria-label="Zazen"
      className={cn("flex h-10 shrink-0 select-none items-center gap-2 border-b border-line-1 bg-ground pl-3", drag ? "pr-0" : "pr-2")}>
      <LogoMark bold className="size-4 shrink-0" />
      <span className="label-tech shrink-0 text-faint">Zazen</span>
      {children ?? <div className="h-full flex-1" />}
      <WindowControls className="ml-1" />
    </div>
  )
}

/** The title bar alone, for the moments before the app exists (the "Starting…" screen); desktop window only. */
export function WindowFrame() {
  const bridge = useWindowBridge()
  if (!bridge) return null
  return (<><TitleBar /><ResizeEdges /></>)
}
