import { useEffect, useRef } from "react"
import { SmokePlane as Engine } from "@/lib/smoke-plane"
import { LogoMark } from "./Logo"

const REDUCED = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches

/** The empty state's background toy (lib/smoke-plane.ts): fills its parent, sits behind the content, ignores the
 *  pointer, and stops when unmounted. With reduced motion it is a faint, still ensō instead. */
export function SmokePlane() {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const host = box.current
    if (REDUCED || !host) return
    const canvas = document.createElement("canvas")
    canvas.setAttribute("aria-hidden", "true")
    canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none"
    let engine: Engine
    try { engine = new Engine(canvas) } catch { return }
    host.appendChild(canvas)
    const fit = () => engine.resize(host.clientWidth, host.clientHeight)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(host)
    engine.start()
    ;(window as any).__plane = engine                    // for scripts/shoot.mjs
    return () => { ro.disconnect(); engine.stop(); canvas.remove() }
  }, [])
  if (REDUCED) return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center" aria-hidden="true">
      <LogoMark className="size-16 opacity-25" />
    </div>
  )
  return <div ref={box} className="pointer-events-none absolute inset-0" aria-hidden="true" />
}
