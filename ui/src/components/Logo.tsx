import { useId } from "react"
import { cn } from "@/lib/utils"
import { ENSO, ENSO_BOLD, ENSO_BOLD_START, ENSO_START } from "./enso-paths"

/** The Zen Harness mark: an ensō, one brush stroke with an open gap at the top right, a loaded start and a
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

/** The wordmark: ZEN (bright) HARNESS (quiet), technical mono, wide tracking. */
export function Wordmark({ className, compact }: { className?: string; compact?: boolean }) {
  return (
    <span className={cn("mono flex items-baseline gap-[0.45em] select-none uppercase leading-none", className)}>
      <span className="text-ui font-semibold tracking-[0.18em] text-foreground">Zen</span>
      {!compact && <span className="text-ui font-medium tracking-[0.18em] text-muted-foreground">Harness</span>}
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
