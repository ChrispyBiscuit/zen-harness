import { useId } from "react"
import { cn } from "@/lib/utils"
import { ENSO, ENSO_BOLD, ENSO_BOLD_START, ENSO_START } from "./enso-paths"

/** The Zazen mark: an ensō, one brush stroke with an open gap at the top right, a loaded start and a
 *  dry-brush tail. Near-white ink; the brand accent is only a faint tint where the brush lands. `bold` is the
 *  simpler, heavier stroke that still reads at 16–20 px; `textured` adds a dry-paper edge for large sizes only. */
export function LogoMark({ className, bold, textured }: { className?: string; bold?: boolean; textured?: boolean }) {
  const id = useId().replace(/:/g, "")
  const paths = bold ? ENSO_BOLD : ENSO
  const [cx, cy] = bold ? ENSO_BOLD_START : ENSO_START
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7 shrink-0", className)} aria-hidden="true">
      <defs>
        <radialGradient id={`${id}-ink`} gradientUnits="userSpaceOnUse" cx={cx} cy={cy} r="14">
          <stop offset="0" style={{ stopColor: "color-mix(in oklab, var(--brand-a) 60%, var(--ink))" }} />
          <stop offset="0.38" style={{ stopColor: "var(--ink)" }} />
        </radialGradient>
        {textured && (
          <filter id={`${id}-dry`} x="-10%" y="-10%" width="120%" height="120%">
            <feTurbulence type="fractalNoise" baseFrequency="1.6" numOctaves="2" seed="4" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="0.35" />
          </filter>
        )}
      </defs>
      <g fill={`url(#${id}-ink)`} filter={textured ? `url(#${id}-dry)` : undefined}>
        {paths.map((d, i) => <path key={i} d={d} />)}
      </g>
    </svg>
  )
}

const INK_SEGMENTS = 40
const INK_LAP_S = 2.6

/** "Working, nothing to show yet": an ensō being painted and drying as it goes. The ring is fixed segments; each is
 *  inked at full width as the brush passes (a slightly loaded head), then thins and fades, so the brush circles
 *  clockwise chasing its own fading tail — nothing rotates. Reduced motion: the still mark instead. */
export function InkRing({ className }: { className?: string }) {
  const r = 11
  const step = 360 / INK_SEGMENTS
  const pt = (deg: number) => {
    const a = ((deg - 90) * Math.PI) / 180                  // 0° = 12 o'clock, clockwise
    return `${(16 + r * Math.cos(a)).toFixed(2)} ${(16 + r * Math.sin(a)).toFixed(2)}`
  }
  return (
    <span className={cn("relative inline-flex size-5 shrink-0", className)} aria-hidden="true">
      <svg viewBox="0 0 32 32" className="ink-ring size-full">
        {Array.from({ length: INK_SEGMENTS }, (_, i) => {
          const a0 = i * step
          return (
            <path key={i} d={`M${pt(a0)} A${r} ${r} 0 0 1 ${pt(a0 + step + 0.6)}`}
              className="ink-seg" fill="none" stroke="var(--ink)" strokeLinecap="butt"
              style={{ animationDelay: `${((i / INK_SEGMENTS) - 1) * INK_LAP_S}s`, animationDuration: `${INK_LAP_S}s` }} />
          )
        })}
      </svg>
      <LogoMark bold className="ink-still absolute inset-0 size-full" />
    </span>
  )
}

/** The wordmark: ZAZEN, technical mono, wide tracking. */
export function Wordmark({ className }: { className?: string; compact?: boolean }) {
  return (
    <span className={cn("mono flex items-baseline select-none uppercase leading-none", className)}>
      <span className="text-ui font-semibold tracking-[0.18em] text-foreground">Zazen</span>
    </span>
  )
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <LogoMark className="size-7" />
      <Wordmark />
    </span>
  )
}
