import { cn } from "@/lib/utils"
import type { Tone } from "@/lib/tone"

/** A thin level bar coloured by state tone (meters, context gauge, checklist progress). The fill moves with a
 *  transform, not a width change, so updates stay off the layout path. `marker` draws a tick at that percent. */
export function MeterBar({ pct, tone, marker, className, label }: {
  pct: number; tone: Tone; marker?: number; className?: string; label?: string
}) {
  const v = Math.max(0, Math.min(100, pct || 0))
  return (
    <div role="meter" aria-label={label} aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}
      className={cn("relative h-1.5 overflow-hidden rounded-full bg-surface-3", className)}>
      <div data-tone={tone} className="tone-fill h-full w-full origin-left rounded-full transition-transform duration-(--dur-slow) ease-(--ease-out)"
        style={{ transform: `scaleX(${v / 100})` }} />
      {marker != null && <div className="absolute top-0 h-full w-px bg-foreground/50" style={{ left: `${marker}%` }} />}
    </div>
  )
}
