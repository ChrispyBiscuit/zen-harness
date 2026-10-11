// Native views placed over a panel by the desktop window (qt_browser.py): the agent's browser and its DevTools.
// The panel renders an empty box; this hook tells the window where that box is, and hides the native view while the
// box isn't showing or an app menu/dialog would end up underneath it.
import { useEffect, useState, type RefObject } from "react"

export type NativeBridge = {
  setRect: (x: number, y: number, w: number, h: number, visible: boolean) => void
  openDevTools?: () => void
  setDevToolsRect?: (x: number, y: number, w: number, h: number, visible: boolean) => void
  popOut?: () => void
  popIn?: () => void
  isPoppedOut?: (cb: (v: boolean) => void) => void
}

/** The window's bridge (window.zenBrowser), once QWebChannel is ready; null in a normal browser. */
export function useNativeBridge() {
  const [bridge, setBridge] = useState<NativeBridge | null>(() => (window as any).zenBrowser ?? null)
  useEffect(() => {
    if (bridge) return
    const on = () => setBridge((window as any).zenBrowser ?? null)
    window.addEventListener("zen-window-ready", on)
    return () => window.removeEventListener("zen-window-ready", on)
  }, [bridge])
  return bridge
}

/** Menus and dialogs of the app would be hidden under a native view: step aside while one is open. */
const OVERLAY = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]'

// only one mounted box per slot places its view (a panel can be mounted twice while the layout moves)
const owners: Record<string, number> = {}

export function useNativeSlot(slot: string, box: RefObject<HTMLElement | null>, enabled: boolean,
                              report: ((x: number, y: number, w: number, h: number, visible: boolean) => void) | undefined) {
  useEffect(() => {
    if (!enabled || !report) return
    const me = (owners[slot] = (owners[slot] || 0) + 1)
    let frame = 0
    let lastKey = ""
    const send = () => {
      frame = 0
      if (owners[slot] !== me) return
      const el = box.current
      const r = el?.getBoundingClientRect()
      const visible = !!el && !!r && r.width > 40 && r.height > 40 && el.offsetParent !== null &&
        document.visibilityState === "visible" && !document.querySelector(OVERLAY)
      const key = visible && r ? `${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.width)},${Math.round(r.height)}` : "hidden"
      if (key === lastKey) return
      lastKey = key
      report(r?.left ?? 0, r?.top ?? 0, r?.width ?? 0, r?.height ?? 0, visible)
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(send) }
    const ro = new ResizeObserver(schedule)
    if (box.current) ro.observe(box.current)
    const mo = new MutationObserver(schedule)          // dialogs/menus mount as portals directly under <body>
    mo.observe(document.body, { childList: true })
    window.addEventListener("resize", schedule)
    document.addEventListener("visibilitychange", schedule)
    // the tray folding/peeking and panel open/close animations move the box without resizing it
    document.addEventListener("transitionend", schedule, true)
    document.addEventListener("animationend", schedule, true)
    document.addEventListener("transitionstart", schedule, true)
    const t = setInterval(schedule, 1000)             // panels moved by the dock layout
    schedule()
    return () => {
      ro.disconnect(); mo.disconnect(); clearInterval(t)
      window.removeEventListener("resize", schedule)
      document.removeEventListener("visibilitychange", schedule)
      document.removeEventListener("transitionend", schedule, true)
      document.removeEventListener("animationend", schedule, true)
      document.removeEventListener("transitionstart", schedule, true)
      if (frame) cancelAnimationFrame(frame)
      if (owners[slot] === me) report(0, 0, 0, 0, false)
    }
  }, [slot, box, enabled, report])
}
