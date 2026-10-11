import { useCallback, useEffect, useRef } from "react"
import { SquareCode } from "lucide-react"
import { useNativeBridge, useNativeSlot } from "@/lib/nativeSlot"

/** Chrome's DevTools for the agent's browser page, docked like any other panel (the window places the real
 *  DevTools view over this box). Opened from the browser panel's DevTools button or F12 in the page. */
export function DevToolsView() {
  const box = useRef<HTMLDivElement>(null)
  const native = useNativeBridge()
  const place = useCallback((x: number, y: number, w: number, h: number, v: boolean) =>
    native?.setDevToolsRect?.(x, y, w, h, v), [native])
  useEffect(() => { native?.openDevTools?.() }, [native])            // attach to the page shown right now
  useNativeSlot("devtools", box, !!native?.setDevToolsRect, place)
  return (
    <div ref={box} className="relative h-full overflow-hidden bg-background">
      <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-ui text-muted-foreground">
        <SquareCode className="size-6 opacity-60" />
        {native?.setDevToolsRect
          ? "Opening DevTools for the agent's browser page…"
          : "DevTools for the agent's browser are available in the desktop app."}
      </div>
    </div>
  )
}
